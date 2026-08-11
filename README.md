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
sudo vi /etc/sysconfig/tsrs-web        # port + any host override; survives upgrades
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

The site file default assumes the panel host is on the IOC's subnet. Override in
`/etc/sysconfig/tsrs-web` if not:

| Panel host | Config |
|---|---|
| Same subnet as IOC | site-file default — nothing to do |
| Different subnet | `EPICS_CA_ADDR_LIST=` (blank it), `EPICS_CA_NAME_SERVERS=<ioc-ip>:5064`, `EPICS_CA_AUTO_ADDR_LIST=NO` |

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

## Sites

One RPM serves both telescopes. At start, `resolve-site.sh` picks the address
set from `GEMINI_SITE` and merges it with your host overrides:

```
/usr/share/tsrs-screen/site-MK.env   package-owned site facts (upgrades update these)
/etc/sysconfig/tsrs-web              host overrides, %config(noreplace), WINS
        ↓  merged at every start
/run/tsrs-web.env                    what the container actually got
```

Site is resolved first-match: `TSRS_SITE` in `/etc/sysconfig/tsrs-web` →
`$GEMINI_SITE` → a `GEMINI_SITE=` assignment scraped from `/etc/profile.d/*.sh`
or `/etc/environment` → **`MK`**. The scrape exists because `GEMINI_SITE` is a
login-shell variable and systemd services never see it; the assignment is
scraped rather than sourced, so nothing else in those scripts runs.

⚠️ **CP is not commissioned.** `site-CP.env` carries
`TSRS_SITE_UNCONFIGURED=1` and the service refuses to start there. That is
deliberate: falling back to MK would render a readiness screen from the wrong
mountain. Fill in the CP addresses, verify with `tsrs-ca-probe`, delete that
line.

The `bfo:` prefix is still baked into `tsrs_indicators.csv`,
`compliance_additions.csv` and `tsrs.config.json` — **not** a config flag. If CP
uses a different prefix, the CSVs must be regenerated from CP's `.opi` files;
no site file can substitute.

`cat /run/tsrs-web.env` after a start shows exactly what was resolved.

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

`%global specver` in `packaging/tsrs-screen.spec` is the single source of truth.
Bump it, commit, then use any one of:

- **Actions → release → Run workflow**
- `git tag v<specver> && git push origin v<specver>`
- `./packaging/release.sh` (from a laptop; `--dry-run` to preview)

No version is ever typed. All three read `specver`, and a tag that disagrees
with it is rejected — so what ships is only ever what the committed spec says.

| Artifact | Identifier |
|---|---|
| RPM | `tsrs-screen-<specver>-1.el9.noarch` |
| Image | `ghcr.io/gemini-rtsw/tsrs_screen:<specver>` (what the unit pins) |
| Git tag | `v<specver>` |

`release.yml` calls `ci.yml` as a reusable workflow — a release runs the same
drift check, smoke test and image build as any commit, then publishes the exact
RPM artifact CI verified. It refuses an already-released version, or a `specver`
with anything but digits and dots (`-` is RPM's version/release separator).

`ci` alone runs on every push: builds and verifies the RPM as an artifact,
publishes nothing.

Same scripts locally — green laptop, green pipeline:

```bash
./packaging/build-rpm.sh      # version from the spec
./packaging/verify-rpm.sh     # pin, upgrade/downgrade, site resolution, unit parse
```

Both run in a pinned `rockylinux:9.3` with `--platform linux/amd64`, so an Apple
Silicon laptop builds the same package as the runner.

Publishing needs an `RPM_REPO_TOKEN` secret — a **classic** PAT with
`write:packages`, `read:packages`, `repo`. Fine-grained tokens do not reliably
grant GHCR package writes.

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
