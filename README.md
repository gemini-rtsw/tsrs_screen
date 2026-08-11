# TSRS Status Panel

Web replacement for the TSRS (Telescope Structure Readiness System) status
screen, currently a CS-Studio 3.1 BOY display running on EL7.

The screen is a **read-only annunciator**: 68 EPICS channels from the BFO PLC
rendered as Ready / Not Ready indicators, plus the current Observatory Mode.
It has no write path, and the replacement is structurally incapable of one —
no `caput`, no write endpoint, no code path that can set a channel. Keep it
that way; TSRS shows state, the PLC and GIS enforce it.

Requirements served: **REQ-TSRS-0210** (status screen at the observatory
entrance) and **REQ-TSRS-0211** (dedicated EPICS copy of that screen).

## Deploy to production

Target: an **x86_64** host on the control network, running the Docker daemon.
Best placed on the display node itself, so the failure domain stays what it is
today — one box.

```bash
# 1. Authenticate to GHCR (or make the package public in repo → Packages)
docker login ghcr.io -u <gh-user>          # PAT with read:packages

# 2. Install the service
sudo curl -fsSL -o /etc/systemd/system/tsrs-web.service \
  https://raw.githubusercontent.com/gemini-rtsw/tsrs_screen/main/deploy/tsrs-web.service
sudoedit /etc/systemd/system/tsrs-web.service    # pick the CA addressing block -- see below
sudo systemctl daemon-reload
sudo systemctl enable --now tsrs-web

# 3. Verify: want ca_with_values == ca_total
curl -s localhost:8090/api/healthz | python3 -m json.tool
```

Then browse to `http://localhost:8090`.

```bash
systemctl status tsrs-web
journalctl -u tsrs-web -f
sudo systemctl stop tsrs-web       # rollback; nothing else is touched
```

Podman hosts: use `deploy/tsrs-web.container` (Quadlet) in
`/etc/containers/systemd/` instead. Use one unit or the other, never both.

### Reaching the IOC

The BFO IOC is `bfo-mk-ioc` on **`mkosioc-lv1` = 10.2.2.49**, CA port 5064.
How you address it depends entirely on which VLAN the panel host is on, and
getting this wrong is the single most likely reason for `ca_connected: 0`.

**On the control VLAN (10.2.2.0/24)** — normal EPICS, use broadcast:

```
EPICS_CA_ADDR_LIST="10.2.2.255 10.2.10.21"
EPICS_CA_AUTO_ADDR_LIST=YES
```

**Off the control VLAN** (e.g. `mkoswgdkr-lv1`, 10.2.71.15) — use TCP name
resolution and do *not* set `EPICS_CA_ADDR_LIST` at all:

```
EPICS_CA_NAME_SERVERS=10.2.2.49:5064
EPICS_CA_AUTO_ADDR_LIST=NO
```

Two traps here, both of which cost a day:

1. **`10.2.2.255` is a broadcast and does not route.** From another subnet it
   simply never arrives, and CA reports nothing — no error, no log line.
2. **Unicast UDP to `10.2.2.49` does not work either**, which is the
   counter-intuitive part. `mkosioc-lv1` runs *two* IOCs, `bfo-mk-ioc` and
   `toptica-mk-ioc`. Both bind UDP 5064 with `SO_REUSEADDR`. A broadcast
   datagram is delivered to every such socket; a **unicast** datagram is
   delivered to only one — in practice toptica, which correctly answers
   `CA_PROTO_NOT_FOUND` for every `bfo:` channel. The failure looks exactly
   like a firewall problem and is not one.

TCP name resolution avoids trap 2 because only one process can hold TCP 5064,
and on `mkosioc-lv1` that is `bfo-mk-ioc` (verified 2026-08-11, pid 25611).

**That last point is a known fragility, not a design.** TCP 5064 goes to
whichever IOC starts first. If both restart and toptica wins, the panel goes to
NO DATA and nothing in the log says why. The durable fix is a pinned
`EPICS_CAS_SERVER_PORT` for `bfo-mk-ioc`, which is an IOC-side change owned by
whoever runs `mkosioc-lv1`. Until then, prefer deploying on the control VLAN.

Firewall for off-VLAN hosts: TCP 5064 (search *and* data) plus UDP 5065
(beacons) to 10.2.2.49. The UDP rule must be stateful — search replies return
to an ephemeral source port, not to 5064, so a rule pinned to 5064/5065 on both
ends silently drops them.

Gemini South: change these addresses for the CP site.

To verify addressing before touching the unit:

```bash
docker run --rm --network host \
  -e EPICS_CA_NAME_SERVERS=10.2.2.49:5064 -e EPICS_CA_AUTO_ADDR_LIST=NO \
  ghcr.io/gemini-rtsw/tsrs_screen:latest \
  python -c "import epics; pv=epics.PV('bfo:mcsStatus'); print(pv.wait_for_connection(timeout=10), pv.get())"
```

Port 8090 in the shipped unit (8080 was already taken on `mkoswgdkr-lv1`).
`--network host` means **no port isolation**, so on a shared docker host
something may already hold it — the gateway then exits 1 with "address already
in use". Check with `ss -ltnp` and set `TSRS_PORT` (and `TSRS_BIND`, default
`127.0.0.1` in the unit) to something free.

### Five things that will bite you

1. **`--network host` is required, not preferred.** CA name resolution and IOC
   beacons do not survive a bridged/NAT network. On bridge networking the panel
   sits at NO DATA forever with nothing in the log to explain why. See
   "Reaching the IOC" above — addressing is the most common deployment failure.
2. **x86_64 only.** pyepics ships an x86-64 `libca`; on arm the container
   starts and then dies on the first CA call.
3. **Never run the simulator on the control network.** It serves the same 68
   channel names — duplicates break CA name resolution for the real screen too.
   `docker-compose` is for laptops.
4. **Parallel-run it first.** Leave CS-Studio running and compare the 11 summary
   LEDs side by side. Both are read-only, so there is no cutover risk, and it is
   the only real correctness check available.
5. **`docker pull` in the unit runs as root**, which has no GHCR credentials
   even if your own user does. Harmless while the image is present locally (the
   `-` prefix tolerates the failure), but auto-update on restart will not work
   until the package is public or root is logged in.

### Expect up to five channels not to connect

`bfo:cond1bits.B7/.B9/.BA`, `bfo:cond2bits.B2`, `bfo:cond5bits.B4` are the
compliance additions below. They have never appeared on a screen, so this is the
first time anything has asked the IOC for them. If they do not connect, that is a
real finding — the design specifies them but the IOC does not publish them,
turning a display fix into an IOC change. Set `include_compliance_additions:
false` in `tsrs.config.json` and re-run `tools/gen_panel.py` to drop them
meanwhile.

## Local development

```bash
docker-compose up -d --build      # simulator + gateway
open http://localhost:8080
```

The simulator serves all 76 channels over real Channel Access, so the gateway
exercises the actual CA client path rather than a mock. About 8% of bits read
Not Ready and the set rotates every 6 s so every indicator gets exercised.

**The test that matters most:**

```bash
docker stop tsrs-sim
```

Within ~1 s every indicator must switch to a hatched **NO DATA** pill and a red
banner must appear. Values go to `None`, not to their last-known state, and no
indicator falls through to "Not Ready" — a comms failure must never be able to
fake a plant condition in either direction. If it ever shows stale green LEDs
instead, the liveness logic is broken; that failure mode is worse than a blank
screen, because the point of the display is to be trusted before a mode switch.

`docker start tsrs-sim` recovers in ~6 s.

## How it works

```
BFO PLC ──CA monitors──> gateway (in-memory cache) ──HTTP GET /api/status──> panel
                          FastAPI + pyepics, 1 process        polled 2 Hz
```

Acquisition is event-driven (CA monitors, exactly as CS-Studio does today) and
adds no IOC load. Only the browser polls, so the poll rate affects display
latency, never acquisition.

**Single process is mandatory.** The CA monitors populate one in-memory cache;
multiple workers would mean multiple independent CA connections and divergent
caches. Run `uvicorn --workers 1`, and do not put gunicorn in front of it.

### Liveness — read before touching the freshness logic

CA monitors fire only when a value **changes**. A bit that has read Ready for
three days produces no updates at all, so "time since last update" is *not* a
staleness signal — using it would grey out every healthy channel. Liveness
comes from three independent layers:

1. **CA connection state**, per channel. libca/caproto detect a dead IOC via TCP
   plus server beacons. Authoritative for "is the IOC talking to us".
2. **Gateway reachability**, whole panel. A failed `fetch` blanks everything.
3. **PLC heartbeat** — *not yet wired, see below.*

A fourth mechanism guards the client itself. A CA client can wedge in ways it
never recovers from — caproto's search-retry thread dies on a transient DNS
failure, after which no channel is ever searched for again. The panel fails
loud (all NO DATA), which is safe, but it would stay that way until a human
noticed. So a watchdog rebuilds the CA client after `TSRS_CA_WATCHDOG_S`
(default 60 s) with *zero* channels connected. The trigger is deliberately zero,
never a partial outage: some channels being down is a plant condition, not a
client fault. Rebuild count is exposed as `ca_rebuilds` on `/api/healthz`; a
steadily climbing value means the IOC is unreachable, not that the gateway is
broken.

**Dev stack addressing:** `EPICS_CA_ADDR_LIST` points at a *static IP*
(`172.28.7.2`), not the container name, on purpose. With a name, stopping the
simulator removes it from Docker's embedded DNS, and caproto's search thread
dies as described above — the client then never reconnects even after the IOC
returns. An IP cannot fail to resolve, and it mirrors production, where the
address list holds literal addresses.

## Source of truth

`tsrs_indicators.csv` — 82 indicators over 12 screens, 68 distinct PVs,
extracted from the as-built `.opi` files by `tools/extract_opi.py`. The panel
and the gateway's channel list are both generated from it, so they cannot drift
(CI fails the build if the committed output is stale).

CSV rather than YAML deliberately: it diffs cleanly in git, needs no
dependency, and opens in Excel — which is where every TSRS design document
already lives.

```bash
python3 tools/extract_opi.py      # .opi  -> tsrs_indicators.csv   (needs cssapp checked out alongside)
python3 tools/gen_panel.py        # CSVs  -> static/ + gateway/channels.json
```

`reference/` holds the 2015 design authority, frozen into the repo:
`plc_bits.csv` (73 PLC condition bits and which summary bit each feeds),
`plc_summary_bits.csv` (the 13 summary bits), `gaps.csv` (generated).

The PLC↔EPICS mapping, verified against six independent bits in the as-built
screens:

```
bfo:cond{N}bits.B{h}  ⇔  PLC B3/(64 + (N-1)*16 + h)
```

## Compliance findings

Three gaps between the 2015 design and the as-built screen. The first two are
closed by `compliance_additions.csv` (kept separate from the as-built map so
"as-built" and "proposed" never blur); every PV it uses already exists in the
`bfo` condition words, so no IOC change is needed.

**1. LOTO drill-down is incomplete — the significant one.** `LOTO OK` (`B3/264`)
is fed by seven bits; `loto_detail` displays four. Missing: bottom shutter,
east vent gate, and west vent gate interlock keys (`B3/71`, `B3/73`, `B3/74`).
An operator seeing `LOTO: Not Ready` cannot see three of the possible causes —
on a screen whose stated purpose is assessing LOTO status before a mode switch.

**2. Two design summary indicators absent entirely.** `Lights OK` (`B3/262`,
fed by `B3/82`) and `PLC OK` (`B3/267`, fed by `B3/132` "GIS PLC racks OK")
appear in no `.opi` file. The design defines 13 summary bits; the screen
implements 11.

**3. No PLC heartbeat — the residual gap, and it is not closed here.** The
bit-grouping spreadsheet specifies a "BFO PLC heartbeat bit" but gives no PLC
address, and no such PV exists in the as-built screens. Without it, a PLC that
is alive on the network but no longer scanning cannot be detected: CA stays
connected and the last values persist. Ask the controls engineer for the
address, then set `heartbeat_pv` in `tsrs.config.json` — the gateway and panel
already implement the check. **Until then this screen can still hold stale
values in one specific failure mode.** Do not paper over it.

Two caption mismatches were flagged and both are the *screen* being more
correct than the document (`PLX CB cllosed`, `O/Ls not stripped` — typos in the
2015 spreadsheet). No action needed.

## Scope note

`hbfbfotsrs-ld1` is the **Hilo Base Facility** display — the REQ-TSRS-0211
copy. REQ-TSRS-0210 puts a status screen at the *observatory entrance* on the
summit, and `ObsMode.js` in the original workspace is commented "Display
Observatory Mode on the TSRS Entrance Status Screen". **There is very likely a
second host on Mauna Kea running this same screen, also on EL7, and it is the
safety-relevant one.** Sweep `rpm -q cssapp` across the display nodes before
sizing the rollout. Gemini South also deploys `cssapp` (the `CP` branch in
`lhandset.sh`), so confirm whether GS is in scope or a later phase.

## Deployment reference

| Host runtime | Unit | Install to |
|---|---|---|
| **Docker daemon** | `deploy/tsrs-web.service` | `/etc/systemd/system/` |
| podman (EL9) | `deploy/tsrs-web.container` | `/etc/containers/systemd/` |

Use one, not both. The Docker unit runs `docker run --rm` in the foreground
rather than `--restart=always`, so systemd owns the lifecycle; with both,
`systemctl stop` leaves the container running.

**Run it on the display node**, bound to loopback. The failure domain then stays
what it is today: one box. Centralising it invents a new way to blank the panel
for everyone at once, which needs an availability plan first.

The kiosk browser stays on the host — containerising chromium with X/Wayland
access is all pain and no value. It replaces the `Css` invocation in
`/etc/ITOps/tsrs-launcher.sh`, which should come under version control while you
are there.

### CA client backends

| Backend | Use | Notes |
|---|---|---|
| `pyepics` | **production** (default) | Site standard; Gemini already packages `epics_module-pyEpics`. Wheel bundles an **x86-64** libca only. |
| `caproto` | dev / CI only | Pure Python, no libca. The only option on arm64 (Apple Silicon). |

Set with `TSRS_CA_BACKEND`. Images are built `linux/amd64` only, on purpose: an
arm64 image starts and then dies on the first CA call. If site policy requires
the Gemini `epics-base` instead of the bundled libca, install it and set
`PYEPICS_LIBCA=/path/to/libca.so` — pyepics will not find it on its own.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/status` | all channel readings + Observatory Mode inputs + heartbeat |
| `GET /api/healthz` | **process** liveness. `ok` is not channel connectivity — a dead IOC is not a sick gateway, and restarting on that would drop the panel for nothing. CA state is reported alongside as `ca_connected` / `ca_ok`. |

## Layout

```
tools/extract_opi.py          .opi -> tsrs_indicators.csv
tools/gen_panel.py            CSVs -> static/ + gateway/channels.json
tools/plc_bits_from_xlsx.py   design spreadsheet -> reference/*.csv
gateway/tsrs_web/app.py       FastAPI + CA monitors (read-only)
sim/tsrs_sim.py               caproto soft IOC serving the 76 channels
deploy/tsrs-web.container     podman Quadlet unit for EL9
reference/                    2015 design authority, frozen
```
