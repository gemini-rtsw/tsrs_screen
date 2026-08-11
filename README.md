# TSRS Status Panel

Web replacement for the TSRS (Telescope Structure Readiness System) CS-Studio
screen. A **read-only annunciator**: 68 EPICS channels from the BFO PLC as
Ready / Not Ready indicators, plus Observatory Mode. There is no write path and
no code that could add one — keep it that way.

Serves REQ-TSRS-0210 (entrance screen) and REQ-TSRS-0211 (EPICS copy).

## Deploy

Target: **x86_64** host with the Docker daemon, ideally the display node itself.

**1. Find your IOC.** None of these are constants — they differ at GS and change
as machines are replaced.

| Fact | How | GN, Aug 2026 |
|---|---|---|
| IOC host / address | ask controls; `getent hosts <host>` | `mkosioc-lv1` = `10.2.2.49` |
| IOC subnet + broadcast | `ip -br addr` **on the IOC host** | `10.2.2.0/24`, `10.2.2.255` |
| Panel host subnet | `ip -br addr` on the panel host | `10.2.71.0/24` — *different* |

```bash
python3 tools/ca_probe.py <ioc-ip> bfo:mcsStatus       # want: FOUND
# no checkout on the host? it ships in the image:
docker run --rm --network host ghcr.io/gemini-rtsw/tsrs_screen:latest \
  python /app/tools/ca_probe.py <ioc-ip> bfo:mcsStatus
```

**2. Install — RPM (preferred).** From the [GHCR yum
repo](https://github.com/gemini-rtsw/gemini-rtsw-repo). Serve it, then install:

```bash
docker run -d --name rpm-repo -p 8081:8080 ghcr.io/gemini-rtsw/rpm-repo:latest
sudo dnf config-manager --add-repo http://localhost:8081/rpm-repo/
sudo dnf install tsrs-screen
sudo vi /etc/sysconfig/tsrs-web        # IOC address + port; survives upgrades
sudo systemctl enable --now tsrs-web
```

Port 8081 because the documented 8080 is often taken. The unit is pinned to the
image tag matching the RPM version, so `rpm -q tsrs-screen` says exactly what
runs, `dnf upgrade` moves it, and `dnf downgrade` is a real rollback.

**Install — no RPM repo.** Target hosts reach GHCR but **not github.com**, so
the unit also ships inside the app image; `docker` grants the root-equivalent
write that covers restricted `sudo`:

```bash
docker run --rm --user 0 -v /etc/systemd/system:/out \
  ghcr.io/gemini-rtsw/tsrs_screen:latest \
  cp /app/deploy/tsrs-web.service /out/tsrs-web.service
sudo systemctl edit --full tsrs-web    # --force --full to paste from scratch
sudo systemctl daemon-reload && sudo systemctl enable --now tsrs-web
```

This path has no `/etc/sysconfig/tsrs-web`, so site settings go in the unit and
you own the drift. Podman: `/app/deploy/tsrs-web.container` in
`/etc/containers/systemd/`. One unit or the other, never both.

**3. Verify.**

```bash
curl -s localhost:8090/api/healthz | python3 -m json.tool   # ca_with_values == ca_total (76/76 at GN)
docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' tsrs-web   # must be "no"
sudo systemctl stop tsrs-web            # rollback; nothing else touched
```

Restart policy must be `no` — otherwise the daemon and systemd fight over the
container at boot. A real reboot is the only proof it comes back.

## Reaching the IOC

The top cause of `ca_connected: 0`, and it fails silently.

| Panel host | Config |
|---|---|
| Same subnet as IOC | `EPICS_CA_ADDR_LIST="<ioc-bcast>"`, `EPICS_CA_AUTO_ADDR_LIST=YES` |
| Different subnet | `EPICS_CA_NAME_SERVERS=<ioc-ip>:5064`, `EPICS_CA_AUTO_ADDR_LIST=NO`, **no** `ADDR_LIST` |

Three traps:

- **Broadcasts don't route.** From another subnet the broadcast never arrives.
- **Unicast UDP can hit the wrong IOC.** An IOC host often runs several IOCs all
  bound to UDP 5064 with `SO_REUSEADDR`; broadcast reaches all, unicast reaches
  one. The wrong one answers `NOT_FOUND` — looks exactly like a firewall block.
  TCP name resolution avoids this: only one process holds TCP 5064
  (`ss -lntup | grep 5064` — must be the BFO IOC).
- **Cross-subnet firewall** needs TCP 5064 (search + data) and UDP 5065
  (beacons), and the UDP rule **must be stateful** — replies return to an
  ephemeral source port, not 5064. `ca_probe.py` prints that port.

⚠️ TCP 5064 goes to whichever IOC starts first. If the other wins after a
restart, the panel goes to NO DATA with nothing in the log. Durable fix is a
pinned `EPICS_CAS_SERVER_PORT` on the IOC side; prefer the IOC's own subnet.

## Other sites

The `bfo:` prefix is baked into `tsrs_indicators.csv`,
`compliance_additions.csv` and `tsrs.config.json` — it is **not** a config flag.
New IOC address = edit the unit. New telescope = regenerate the CSVs from that
site's `.opi` files.

## Gotchas

- **`--network host` is required.** CA name resolution and beacons don't survive
  bridge/NAT — panel sits at NO DATA with nothing in the log.
- **No port isolation.** Unit ships `TSRS_PORT=8090` because 8080 was taken.
  Check `ss -ltnp` first.
- **x86_64 only.** pyepics bundles an x86-64 `libca`; arm dies on first CA call.
- **Never run the simulator on the control network** — duplicate channel names
  break resolution for the real screen.
- **`docker pull` in the unit runs as root**, which has no GHCR credentials, so
  unattended reboots won't pick up new images. `sudo docker login ghcr.io` once.
- **Parallel-run against CS-Studio before cutover.** Both are read-only, so it's
  free, and it's the only real correctness check.

## Local development

```bash
docker-compose up -d --build      # simulator + gateway
open http://localhost:8080
```

**The test that matters:** `docker stop tsrs-sim`. Within ~1 s every indicator
must go to a hatched **NO DATA** pill with a red banner. Values must go to
`None`, never to their last-known state, and never fall through to "Not Ready" —
a comms failure must not be able to fake a plant condition in either direction.
`docker start tsrs-sim` recovers in ~6 s.

## How it works

```
BFO PLC ──CA monitors──> gateway (in-memory cache) ──GET /api/status──> panel
                          FastAPI + pyepics, 1 process       polled 2 Hz
```

**Single process is mandatory** — the monitor cache is per-process state. Run
`uvicorn --workers 1`, no gunicorn in front.

**Liveness.** CA monitors fire only on *change*, so update age is not a
staleness signal — a stable bit is silent for days. Liveness comes from CA
connection state per channel, gateway reachability for the whole panel, and
(once wired) the PLC heartbeat. A watchdog rebuilds the CA client after
`TSRS_CA_WATCHDOG_S` (60 s) at *zero* connected channels — zero, never partial,
because partial is a plant condition. Climbing `ca_rebuilds` = IOC unreachable.

| Endpoint | Purpose |
|---|---|
| `GET /api/status` | channel readings, mode inputs, heartbeat |
| `GET /api/healthz` | **process** liveness. `ok` is not CA connectivity — a dead IOC is not a sick gateway. CA state reported as `ca_connected` / `ca_ok`. |

## Source of truth

`tsrs_indicators.csv` — 82 indicators, 12 screens, 68 PVs, extracted from the
as-built `.opi` files. Panel and channel list are both generated from it, so
they cannot drift (CI fails on stale output).

```bash
python3 tools/extract_opi.py   # .opi -> tsrs_indicators.csv (needs cssapp alongside)
python3 tools/gen_panel.py     # CSVs -> static/ + gateway/channels.json
python3 tools/ca_probe.py      # "does this host serve this PV?" -- no EPICS needed
```

## Releasing

`git tag v1.2.0 && git push --tags`. CI builds the image, then the RPM pinned to
that same tag, and registers it with `gemini-rtsw-repo`.

CI **calls the same two scripts** you run locally, so a green laptop means a
green pipeline:

```bash
./packaging/build-rpm.sh 1.2.0     # -> rpmout/tsrs-screen-1.2.0-1.el9.noarch.rpm
./packaging/verify-rpm.sh 1.2.0    # install, pin, upgrade/downgrade, unit parse
```

Every push builds, verifies and uploads the RPM as a workflow artifact; only
`v*` tags publish it to the repo. Untagged builds are version `0.0.0` and pin an
image tag that was never published — they exist to prove the packaging works,
**not to install**. Both run in a pinned `rockylinux:9.3` container
with `--platform linux/amd64`, so an Apple Silicon laptop produces the same
package as the runner. `verify-rpm.sh` builds a `+1` version itself to test that
an upgrade moves the image pin while `/etc/sysconfig/tsrs-web` survives.

Needs an `RPM_REPO_TOKEN` secret (`write:packages`, plus read on
`gemini-rtsw-repo`) for the publish step.

`reference/` holds the frozen 2015 design authority. PLC↔EPICS mapping, verified
against six independent bits: `bfo:cond{N}bits.B{h}` ⇔ `PLC B3/(64 + (N-1)*16 + h)`.

## Compliance findings

Gaps vs. the 2015 design. 1 and 2 are closed by `compliance_additions.csv` (kept
separate so "as-built" and "proposed" never blur); all 76 channels connect on
the real IOC, so no IOC change was needed.

1. **LOTO drill-down incomplete** — `LOTO OK` is fed by 7 bits, the screen showed
   4. An operator could not see three possible causes of `LOTO: Not Ready`.
2. **Two summary indicators absent** — `Lights OK` and `PLC OK`. Design defines
   13 summary bits; the screen implemented 11.
3. **No PLC heartbeat — OPEN, and it blocks sign-off.** The design names a
   heartbeat bit but never assigned it an address. Without it, a PLC that is
   alive on the network but no longer scanning is undetectable: CA stays
   connected and values persist. Get the address from a controls engineer and
   set `heartbeat_pv` in `tsrs.config.json` — gateway and panel already
   implement the check. **Until then the screen can hold stale values in this
   one failure mode.** Do not paper over it.

## Scope

`hbfbfotsrs-ld1` is the Hilo Base copy (REQ-0211). REQ-0210 puts a screen at the
*observatory entrance* on the summit — very likely a second EL7 host, and the
safety-relevant one. Sweep `rpm -q cssapp` across display nodes before sizing a
rollout. GS also deploys `cssapp`; confirm whether it is in scope.

## Layout

```
tools/extract_opi.py       .opi -> tsrs_indicators.csv
tools/gen_panel.py         CSVs -> static/ + gateway/channels.json
tools/ca_probe.py          CA reachability probe (stdlib only)
gateway/tsrs_web/app.py    FastAPI + CA monitors (read-only)
sim/tsrs_sim.py            caproto soft IOC, 76 channels
deploy/                    systemd unit + podman Quadlet
reference/                 2015 design authority, frozen
```
