"""TSRS panel gateway.

Holds live Channel Access monitors on the bfo status channels and serves the
current values as JSON to the browser panel.

Read-only by construction. There is no caput, no write endpoint, and no code
path that can set a channel. TSRS is an annunciator; keep it that way.

Single process by design: the CA monitors populate one in-memory cache, so
multiple workers would mean multiple independent CA connections and divergent
caches. Run with --workers 1. Do not put gunicorn in front of this.
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import threading
import time
from typing import Dict, Optional

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

log = logging.getLogger("tsrs")

HERE = pathlib.Path(__file__).resolve().parent
PKG_ROOT = HERE.parent
CHANNELS = pathlib.Path(os.environ.get("TSRS_CHANNELS", PKG_ROOT / "channels.json"))
CONFIG = pathlib.Path(os.environ.get("TSRS_CONFIG", PKG_ROOT / "tsrs.config.json"))
STATIC = pathlib.Path(os.environ.get("TSRS_STATIC", PKG_ROOT / "static"))


class Reading(BaseModel):
    """One channel's current state as seen by the gateway."""
    value: Optional[float] = None
    connected: bool = False
    severity: int = 0
    ts: float = 0.0          # epoch seconds of last value update (informational)


class Heartbeat(BaseModel):
    configured: bool = False
    ok: bool = True
    age: float = 0.0


class Status(BaseModel):
    now: float
    channels: Dict[str, Reading]
    heartbeat: Heartbeat
    connected: int
    total: int


class Monitor:
    """Live CA monitor set over a fixed channel list.

    Values are replaced wholesale per key (never mutated in place), so the
    single-key reads done by the request handler need no lock: CPython dict
    item assignment is atomic.

    Two Channel Access client backends, selected by TSRS_CA_BACKEND:

      pyepics  (default, PRODUCTION) -- Gemini already packages
               epics_module-pyEpics, so this is the site-standard client.
               Its wheel bundles an x86-64 libca only.

      caproto  (DEV/CI ONLY) -- pure Python, no libca, therefore the only
               option on arm64 machines such as Apple Silicon laptops. Do not
               deploy with this backend without agreeing it with the group; the
               point of pyepics here is institutional familiarity.

    The two are kept behind this one class deliberately: nothing outside
    Monitor knows which client is in use.
    """

    def __init__(self, names, heartbeat_pv=None, heartbeat_timeout=5.0,
                 backend=None):
        self.names = list(names)
        self.heartbeat_pv = heartbeat_pv
        self.heartbeat_timeout = heartbeat_timeout
        self.backend = (backend or os.environ.get("TSRS_CA_BACKEND", "pyepics")).lower()
        self._cache: Dict[str, Reading] = {n: Reading() for n in self.names}
        self._hb_last = 0.0
        self._pvs = []
        self._caproto_pvs = {}
        # caproto holds only weak references to subscriptions and callbacks;
        # without a strong reference here they are garbage-collected and no
        # updates are ever delivered (channels connect, values stay None).
        self._subs = []
        self._ctx = None
        self._started = threading.Event()
        self._last_any_connected = time.time()
        self._rebuilds = 0
        self._watchdog_s = float(os.environ.get("TSRS_CA_WATCHDOG_S", "60"))
        self._lock = threading.Lock()

    def start(self):
        if self.backend == "caproto":
            self._start_caproto()
        elif self.backend == "pyepics":
            self._start_pyepics()
        else:
            raise SystemExit("unknown TSRS_CA_BACKEND=%r (pyepics|caproto)"
                             % self.backend)
        self._started.set()
        log.info("monitoring %d channels via %s", len(self.names), self.backend)
        if self._watchdog_s > 0:
            t = threading.Thread(target=self._watchdog, name="ca-watchdog",
                                 daemon=True)
            t.start()

    # -- watchdog ------------------------------------------------------------
    def _watchdog(self):
        """Rebuild the CA client if it stops seeing anything at all.

        A CA client can wedge in ways that never recover on their own -- e.g.
        caproto's search-retry thread dies on a transient DNS failure, after
        which no channel is ever searched for again. The panel fails loud (all
        NO DATA), which is safe, but it would stay that way until a human
        noticed and restarted the service.

        Rebuilding is cheap and harmless when the IOC really is down: we simply
        keep retrying. The trigger is deliberately "zero channels connected for
        a sustained period", never a partial outage -- some channels being down
        is a plant condition, not a client fault.
        """
        while True:
            time.sleep(min(10.0, max(1.0, self._watchdog_s / 4)))
            try:
                if any(r.connected for r in self._cache.values()):
                    self._last_any_connected = time.time()
                    continue
                idle = time.time() - self._last_any_connected
                if idle < self._watchdog_s:
                    continue
                log.error("no CA channel connected for %.0fs -- rebuilding %s "
                          "client (rebuild #%d)", idle, self.backend,
                          self._rebuilds + 1)
                self._rebuild()
            except Exception:
                log.exception("watchdog iteration failed")

    def _rebuild(self):
        with self._lock:
            self._rebuilds += 1
            self._last_any_connected = time.time()
            try:
                self._teardown()
            except Exception:
                log.exception("teardown during rebuild failed (continuing)")
            try:
                if self.backend == "caproto":
                    self._start_caproto()
                else:
                    self._start_pyepics()
            except Exception:
                log.exception("rebuild failed; will retry on next watchdog tick")

    def _teardown(self):
        for pv in self._pvs:
            try:
                pv.disconnect()
            except Exception:
                pass
        self._pvs = []
        self._subs = []
        self._caproto_pvs = {}
        if self._ctx is not None:
            try:
                self._ctx.disconnect()
            except Exception:
                pass
            self._ctx = None

    def _start_pyepics(self):
        import epics  # imported here so the module can be introspected without CA

        for name in self.names:
            self._pvs.append(epics.PV(
                name,
                auto_monitor=True,
                callback=self._on_value,
                connection_callback=self._on_conn,
                form="native",
            ))

    def _start_caproto(self):
        from caproto.threading.client import Context

        # Quiet caproto's per-channel connection chatter: 76 channels produce
        # 76 INFO lines on every reconnect, which buries real warnings.
        logging.getLogger("caproto").setLevel(logging.WARNING)

        self._ctx = Context()
        pvs = self._ctx.get_pvs(*self.names)
        for name, pv in zip(self.names, pvs):
            self._caproto_pvs[name] = pv
            cb = self._make_caproto_cb(name)
            sub = pv.subscribe(data_type="time")
            sub.add_callback(cb)
            self._subs.append((sub, cb))

    def _make_caproto_cb(self, name):
        def cb(sub, response):
            data = getattr(response, "data", None)
            sev = getattr(getattr(response, "metadata", None), "severity", 0)
            val = None
            try:
                if data is not None and len(data):
                    val = float(data[0])
            except (TypeError, ValueError, IndexError):
                val = None
            self._store(name, val, True, int(sev or 0))
        return cb

    @property
    def rebuilds(self):
        return self._rebuilds

    def _store(self, name, value, connected, severity):
        prev = self._cache.get(name) or Reading()
        self._cache[name] = Reading(value=value, connected=connected,
                                    severity=severity, ts=time.time())
        if name == self.heartbeat_pv and prev.value != value:
            self._hb_last = time.time()

    # -- CA callbacks (called from libca threads) ----------------------------
    def _on_value(self, pvname=None, value=None, severity=0, **kw):
        try:
            v = float(value) if value is not None else None
        except (TypeError, ValueError):
            v = None
        self._store(pvname, v, True, int(severity or 0))

    def _on_conn(self, pvname=None, conn=False, **kw):
        prev = self._cache.get(pvname) or Reading()
        self._cache[pvname] = Reading(
            value=prev.value if conn else None,
            connected=bool(conn),
            severity=prev.severity if conn else 0,
            ts=prev.ts,
        )
        if not conn:
            log.warning("disconnected: %s", pvname)

    # -- snapshot ------------------------------------------------------------
    def snapshot(self) -> Status:
        now = time.time()
        chans = {n: self._cache.get(n) or Reading() for n in self.names}
        # caproto has no connection callback in this client; read the PV's own
        # connection state so a dropped IOC is reflected even with no new data.
        for name, pv in self._caproto_pvs.items():
            up = bool(getattr(pv, "connected", True))
            r = chans.get(name) or Reading()
            if r.connected != up:
                r = Reading(value=r.value if up else None, connected=up,
                            severity=r.severity if up else 0, ts=r.ts)
                self._cache[name] = r
                chans[name] = r
        if self.heartbeat_pv:
            age = now - self._hb_last if self._hb_last else float("inf")
            hb = Heartbeat(configured=True, ok=age <= self.heartbeat_timeout,
                           age=min(age, 1e9))
        else:
            hb = Heartbeat(configured=False, ok=True, age=0.0)
        return Status(
            now=now,
            channels=chans,
            heartbeat=hb,
            connected=sum(1 for r in chans.values() if r.connected),
            total=len(chans),
        )


def load_channels():
    return json.loads(CHANNELS.read_text())


def load_config():
    try:
        return json.loads(CONFIG.read_text())
    except FileNotFoundError:
        return {}


cfg = load_config()
monitor = Monitor(
    load_channels(),
    heartbeat_pv=cfg.get("heartbeat_pv"),
    heartbeat_timeout=cfg.get("heartbeat_timeout_s", 5.0),
)

app = FastAPI(title="TSRS Panel Gateway", version="0.1.0",
              description="Read-only EPICS Channel Access bridge for the TSRS "
                          "status panel (REQ-TSRS-0210 / 0211).")


@app.on_event("startup")
def _startup():
    logging.basicConfig(
        level=os.environ.get("TSRS_LOGLEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log.info("backend=%s EPICS_CA_ADDR_LIST=%s EPICS_CA_AUTO_ADDR_LIST=%s",
             monitor.backend,
             os.environ.get("EPICS_CA_ADDR_LIST", "(unset)"),
             os.environ.get("EPICS_CA_AUTO_ADDR_LIST", "(unset)"))
    monitor.start()


@app.get("/api/status", response_model=Status)
def status() -> Status:
    return monitor.snapshot()


@app.get("/api/healthz")
def healthz():
    """Process liveness -- deliberately NOT channel connectivity.

    A dead IOC is not a sick gateway: restarting this process would not fix it
    and would drop the panel for viewers. So "ok" reports only that we are
    serving, and CA state is reported alongside as data for humans and
    dashboards. Container/systemd health checks should key off "ok".
    """
    s = monitor.snapshot()
    hb = s.heartbeat.model_dump() if hasattr(s.heartbeat, "model_dump") \
        else s.heartbeat.dict()
    return {"ok": True, "backend": monitor.backend,
            "ca_connected": s.connected, "ca_total": s.total,
            "ca_ok": s.connected == s.total,
            "ca_rebuilds": monitor.rebuilds, "heartbeat": hb}


if STATIC.is_dir():
    app.mount("/", StaticFiles(directory=str(STATIC), html=True), name="static")
else:  # pragma: no cover - only when the panel has not been generated yet
    @app.get("/")
    def _no_static():
        return RedirectResponse("/docs")
