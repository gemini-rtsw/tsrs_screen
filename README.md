# TSRS Status Panel

Web replacement for the TSRS (Telescope Structure Readiness System) CS-Studio
screen. A **read-only annunciator**: 68 EPICS channels from the BFO PLC as
Ready / Not Ready indicators, plus Observatory Mode. There is no write path and
no code that could add one — keep it that way.

Serves REQ-TSRS-0210 (entrance screen) and REQ-TSRS-0211 (EPICS copy).

## Install

```bash
docker run -d --name rpm-repo -p 8081:8080 ghcr.io/gemini-rtsw/rpm-repo:latest
sudo dnf config-manager --add-repo http://localhost:8081/rpm-repo/
sudo dnf install --disablerepo='*' --enablerepo='*rpm-repo*' --nogpgcheck tsrs-screen
sudo vi /etc/sysconfig/tsrs-web        # host overrides; survives upgrades
sudo systemctl enable --now tsrs-web
curl -s localhost:8090/api/healthz | python3 -m json.tool    # want ca_with_values == ca_total
```

Port 8081 because 8080 is often taken. `--disablerepo` skips the site repos,
unreachable from some hosts; `--nogpgcheck` because the RPM is unsigned.

The unit is pinned to the image tag matching the RPM version, so `rpm -q
tsrs-screen` says exactly what runs and `dnf downgrade` is a real rollback.

**Upgrade.** The repo container serves the repodata it was pulled with, so
replace it or dnf will not see the new version:

```bash
docker pull ghcr.io/gemini-rtsw/rpm-repo:latest
docker rm -f rpm-repo && docker run -d --name rpm-repo -p 8081:8080 ghcr.io/gemini-rtsw/rpm-repo:latest
sudo dnf upgrade --refresh --disablerepo='*' --enablerepo='*rpm-repo*' --nogpgcheck tsrs-screen
sudo systemctl restart tsrs-web
```

Your `/etc/sysconfig/tsrs-web` edits survive (`%config(noreplace)`); a changed
packaged default arrives as `.rpmnew`.

**The unit pulls as root**, so root needs read access to the image. Simplest is
a public package; otherwise `docker login` as yourself and copy the credential:

```bash
docker run --rm --user 0 -v /root:/r -v "$HOME/.docker/config.json":/c:ro \
  ghcr.io/gemini-rtsw/tsrs_screen:latest sh -c 'mkdir -p /r/.docker && cp /c /r/.docker/config.json'
```

## Configuration

Everything host-specific lives in `/etc/sysconfig/tsrs-web`. The unit is
package-owned and carries no site settings.

| Setting | Default | Change when |
|---|---|---|
| `TSRS_BIND` | `127.0.0.1` | reaching the panel from another machine |
| `TSRS_PORT` | `8090` | the port is taken (`ss -ltnp`) |
| `TSRS_SITE` | *(unset)* | `GEMINI_SITE` is absent or wrong here |
| `EPICS_CA_*` | from the site file | the host is not on the IOC's subnet |

At each start `resolve-site.sh` picks `/usr/share/tsrs-screen/site-<SITE>.env`
and merges your file on top into `/run/tsrs-web.env` — last value wins. Site
order: `TSRS_SITE` → `$GEMINI_SITE` → a `GEMINI_SITE=` line in
`/etc/profile.d/*.sh` → **`MK`**. `cat /run/tsrs-web.env` shows what resolved.

⚠️ **CP is not commissioned.** `site-CP.env` carries `TSRS_SITE_UNCONFIGURED=1`
and the service refuses to start there rather than fall back to MK — a readiness
screen from the wrong mountain is worse than none. Fill in the CP addresses,
verify with `tsrs-ca-probe`, delete that line. The `bfo:` prefix is baked into
the CSVs, so a different prefix means regenerating them, not a setting.

## Reaching the IOC

The top cause of `ca_connected: 0`, and it fails silently.

| Panel host | `/etc/sysconfig/tsrs-web` |
|---|---|
| Same subnet as the IOC | nothing — the site file default is right |
| Different subnet | `EPICS_CA_ADDR_LIST=` (blank it), `EPICS_CA_NAME_SERVERS=<ioc-ip>:5064`, `EPICS_CA_AUTO_ADDR_LIST=NO` |

Check before changing anything: `tsrs-ca-probe <ioc-ip> bfo:mcsStatus` — wants
`FOUND`. Three traps:

- **Broadcasts don't route.** From another subnet it never arrives.
- **Unicast UDP can reach the wrong IOC.** Several IOCs share UDP 5064 on one
  host; broadcast reaches all, unicast reaches one. The wrong one answers
  `NOT_FOUND`, which looks exactly like a firewall block. TCP name resolution
  avoids it — only one process holds TCP 5064 (`ss -lntup | grep 5064`).
- **Cross-subnet firewall** needs TCP 5064 and UDP 5065, and the UDP rule must
  be **stateful** — replies return to an ephemeral port, which `tsrs-ca-probe`
  prints.

⚠️ TCP 5064 goes to whichever IOC starts first. If the other wins after a
restart the panel goes to NO DATA with nothing in the log. The durable fix is a
pinned `EPICS_CAS_SERVER_PORT` on the IOC side.

GN: IOC `mkosioc-lv1` = `10.2.2.49`, subnet `10.2.2.0/24`.

## Testing

```bash
docker-compose up -d --build      # simulator + gateway
open http://localhost:8080        # dev stack is 8080, not 8090
curl -s localhost:8080/api/healthz | python3 -m json.tool   # want 76/76
```

The simulator serves all 76 channels over real Channel Access, so the gateway
exercises the actual CA client path rather than a mock. ~8% of bits read Not
Ready, rotating every 6 s so every indicator gets exercised, and Observatory
Mode cycles Summit → Standby → Base. To hold one mode, set `--period 86400` in
`docker-compose.yml`.

**The test that matters:**

```bash
docker stop tsrs-sim
```

Within ~1 s every indicator must go to a hatched **NO DATA** pill with a red
banner. Values must go to `None`, never to their last-known state, and never
fall through to "Not Ready" — a comms failure must not be able to fake a plant
condition in either direction. `docker start tsrs-sim` recovers in ~6 s.

After editing `static/` or the CSVs, rebuild or you are testing the old panel:
`docker-compose up -d --build gateway`.

⚠️ **Never run the simulator on the control network.** It serves the same
channel names and would break CA name resolution for the real screen.

Test the package itself:

```bash
./gemini-rtsw-ci/build_rpm.sh --el 9 --profile lightweight --spec packaging/tsrs-screen.spec
OUT=$PWD/rpms ./packaging/verify-rpm.sh
```

`verify-rpm.sh` is what CI runs: image pin matches the version, sysconfig is
`%config(noreplace)`, an upgrade moves the pin while keeping host edits, a
downgrade rolls back, site resolution works, the unit parses.

## Releasing

Bump `%global specver` in `packaging/tsrs-screen.spec` and push. Every push to
`main` builds and publishes the RPM and the image.

`Release` carries the commit hash (`0.4.0-1.gite3ef005.el9`), so each build is a
distinct NVRA and `rpm -q` names the exact commit deployed.

CI is the standard gemini-rtsw pipeline — a build hook and a publish hook into
`gemini-rtsw-ci` with `profile: lightweight`. This repo owns no build script.

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

**Caching.** `style.css` and `app.js` carry a content hash and may be cached
forever; `index.html` is served `no-store` because it names those URLs. Without
that a kiosk sits on an old build indefinitely.

| Endpoint | Purpose |
|---|---|
| `GET /api/status` | channel readings, mode inputs, heartbeat |
| `GET /api/healthz` | **process** liveness. `ok` is not CA connectivity — a dead IOC is not a sick gateway. CA state is reported as `ca_connected` / `ca_ok`. |

## Source of truth

`tsrs_indicators.csv` — 82 indicators, 12 screens, 68 PVs, extracted from the
as-built `.opi` files. The panel and the channel list are both generated from
it, so they cannot drift (CI fails on stale output).

```bash
python3 tools/extract_opi.py   # .opi -> tsrs_indicators.csv (needs cssapp alongside)
python3 tools/gen_panel.py     # CSVs -> static/ + gateway/channels.json
python3 tools/ca_probe.py      # "does this host serve this PV?" -- no EPICS needed
```

`reference/` holds the frozen 2015 design authority. PLC↔EPICS mapping, verified
against six independent bits: `bfo:cond{N}bits.B{h}` ⇔ `PLC B3/(64 + (N-1)*16 + h)`.

## Compliance findings

Gaps vs. the 2015 design. 1 and 2 are closed by `compliance_additions.csv`; all
76 channels connect on the real IOC, so no IOC change was needed.

1. **LOTO drill-down incomplete** — `LOTO OK` is fed by 7 bits, the screen showed
   4. An operator could not see three possible causes of `LOTO: Not Ready`.
2. **Two summary indicators absent** — `Lights OK` and `PLC OK`. The design
   defines 13 summary bits; the screen implemented 11.
3. **No PLC heartbeat — OPEN, blocks sign-off.** The design names a heartbeat
   bit but never assigned it an address. Without it a PLC alive on the network
   but no longer scanning is undetectable: CA stays connected and values
   persist. Get the address, set `heartbeat_pv` in `tsrs.config.json` — gateway
   and panel already implement the check. **Until then the screen can hold stale
   values in this one failure mode.**

## Scope

`hbfbfotsrs-ld1` is the Hilo Base copy (REQ-0211). REQ-0210 puts a screen at the
*observatory entrance* — very likely a second EL7 host, and the safety-relevant
one. Sweep `rpm -q cssapp` across display nodes before sizing a rollout.

## Layout

```
tsrs_indicators.csv        source of truth: 82 indicators
tools/gen_panel.py         CSVs -> static/ + gateway/channels.json
tools/extract_opi.py       .opi -> tsrs_indicators.csv
tools/ca_probe.py          CA reachability probe (stdlib only)
gateway/tsrs_web/app.py    FastAPI + CA monitors (read-only)
sim/tsrs_sim.py            caproto soft IOC, 76 channels
deploy/                    unit template, site files, site resolver
packaging/                 spec + verify script
reference/                 2015 design authority, frozen
```
