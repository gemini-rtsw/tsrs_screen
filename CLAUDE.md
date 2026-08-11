# CLAUDE.md — TSRS Status Panel

Read this before changing anything. It records the invariants and the traps that
already cost a day; `README.md` is the human-facing doc, `docs/STATUS.md` is the
current state and open items.

## What this is

A web replacement for the Gemini TSRS (Telescope Structure Readiness System)
status screen — previously a CS-Studio 3.1 BOY display (Eclipse 3.7-era
SWT/GTK2, Java 8) on EL7, which has no EL9 path because GTK2 was removed in EL9
and that CS-Studio line is dead upstream.

It serves **REQ-TSRS-0210** (status screen at the observatory entrance) and
**REQ-TSRS-0211** (dedicated EPICS copy of that screen). This is a
safety-adjacent display: staff read it to assess system status *before* switching
Observatory Mode. Treat correctness of the liveness logic as the top priority.

Source workspace it was extracted from: `../cssapp` (RPM `cssapp`, CS-Studio
workspace). Original design documents: `~/Downloads/TSRS`.

## Invariants — do not break these

1. **Read-only by construction.** No `caput`, no write endpoint, no code path
   that can set a channel. TSRS shows state; the PLC and GIS enforce it. If
   someone asks for a control button, that is a different application.
2. **Single process.** The CA monitors populate one in-memory cache, so multiple
   workers mean multiple independent CA connections and divergent caches.
   `uvicorn --workers 1`. Never put gunicorn in front of it.
3. **Liveness is not update age.** CA monitors fire only when a value *changes*;
   a bit that has read Ready for three days sends nothing. Deriving staleness
   from "time since last update" would grey out every healthy channel. Liveness
   comes from CA connection state, gateway reachability, and (once wired) the
   PLC heartbeat.
4. **A comms failure must never render as a plant condition.** Disconnected
   channels go to `value: null` → hatched NO DATA, never to `0` / "Not Ready"
   and never to a stale "Ready". A panel that holds green LEDs when the IOC dies
   is worse than a blank one.
5. **`healthz.ok` is process liveness, not channel connectivity.** A dead IOC is
   not a sick gateway — restarting on that would drop the panel for viewers and
   fix nothing. CA state is reported separately as `ca_connected` /
   `ca_with_values` / `ca_ok`.
6. **The generated panel must match the CSVs.** CI fails on drift. After editing
   either CSV, run `python3 tools/gen_panel.py` and commit the result.

## Traps that already cost time

- **`connected` does not imply a value.** pyepics fires its connection callback
  *before* the first monitor update; caproto only reaches connected via a value
  callback. Always test `connected && value is not None`. A CI assertion that
  ignored this failed on the x86_64 runner while passing locally on arm64.
- **A CA client can wedge permanently.** caproto's search-retry thread dies on a
  transient DNS failure, after which nothing is ever searched for again. Hence
  the watchdog that rebuilds the client after `TSRS_CA_WATCHDOG_S` (default 60 s)
  with **zero** channels connected. Never trigger it on a partial outage — some
  channels down is a plant condition, not a client fault.
- **Address IOCs by IP, never by hostname**, in `EPICS_CA_ADDR_LIST`. A name that
  stops resolving kills the search thread (above).
- **`--network host` is required.** `EPICS_CA_ADDR_LIST` is a *broadcast*
  address; CA name resolution and IOC beacons do not survive a bridged/NAT
  network. On bridge networking the panel sits at NO DATA forever with nothing in
  the log to explain it.
- **Host networking means no port isolation.** Shared Docker hosts often already
  hold 8080. Use `TSRS_PORT` / `TSRS_BIND`; do not hardcode a port.
- **Images are `linux/amd64` only, deliberately.** pyepics ships an x86-64
  `libca`; an arm64 image starts and then dies on the first CA call. On an Apple
  Silicon laptop use `TSRS_CA_BACKEND=caproto` (pure Python, dev/CI only).
- **Never run the simulator on the control network.** It is a CA *server*
  publishing the same 68 channel names; duplicates break name resolution for the
  real screen too. `docker-compose` is for laptops only.
- **Directed broadcasts do not cross subnets.** `10.2.2.255` works only from a
  host on that VLAN; from anywhere else use unicast to the IOC.

## Source of truth

`tsrs_indicators.csv` — 82 indicators over 12 screens, 68 distinct PVs, extracted
from the as-built `.opi` files. The panel and the gateway's channel list are both
generated from it, so they cannot drift.

`compliance_additions.csv` is kept separate so "as-built" and "proposed" never
blur. `reference/` freezes the 2015 design authority.

CSV rather than YAML on purpose: it diffs cleanly, needs no dependency, and opens
in Excel — where every TSRS design document already lives.

PLC↔EPICS mapping, verified against six independent bits:

```
bfo:cond{N}bits.B{h}  ⇔  PLC B3/(64 + (N-1)*16 + h)
```

## Commands

```bash
python3 tools/extract_opi.py   # .opi -> tsrs_indicators.csv (needs ../cssapp)
python3 tools/gen_panel.py     # CSVs -> static/ + gateway/channels.json
docker-compose up -d --build   # simulator + gateway on localhost:8080
docker stop tsrs-sim           # THE test: all pills must go NO DATA in ~1s
```

## Environment

- **Docker daemon, not podman.** `deploy/tsrs-web.service` is the unit to use;
  `deploy/tsrs-web.container` (Quadlet) is for podman hosts only.
- **Sudo is `systemctl`-only** — no `cp`, and env vars cannot be passed through
  sudo. Install units with `sudo systemctl edit --force --full <name>.service`,
  or write the file in `$HOME` and `sudo systemctl link /abs/path`.
- **`ExecStartPre=docker pull` runs as root**, which has no GHCR credentials even
  when the invoking user does.
- Gemini is moving to a central Docker VM model; a control-VLAN VM on 10.2.2 is
  planned. Centralising moves the failure domain — one outage would blank every
  panel at once, which needs an availability plan for a safety-adjacent display.

## Style

Match the surrounding code. Comments explain *why*, especially where a naive
reading would suggest a simpler-but-wrong implementation — most of the comments
in `app.py` and the deploy files exist because someone (me) already got it wrong
once. Do not delete them to tidy up.
