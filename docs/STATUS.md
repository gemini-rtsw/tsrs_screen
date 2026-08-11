# Status — 2026-08-11

Current state and open items. `CLAUDE.md` holds the durable invariants; this file
changes as work progresses.

## Where things stand

**Working end to end against the simulator.** 76/76 channels connected,
disconnect detected in ~1 s, native reconnect ~6 s, watchdog rebuild verified.

**CI green path:** drift check → CA smoke test (pyepics on the x86_64 runner) →
publish to `ghcr.io/gemini-rtsw/tsrs_screen` (amd64).

**Deployed to `mkoswgdkr-lv1` (10.2.71.15)** via a systemd unit on port 8090,
and as of 2026-08-11 **CA connectivity is solved**. Firewall opened by ITOps
(TCP/UDP 5064–5065 to `mkosioc-lv1`, 10.2.2.49) and verified working in both
directions.

The remaining failure after the firewall was an *addressing* problem, not a
network one, and it is worth recording because it presents exactly like a
firewall block:

- `mkoswgdkr-lv1` is on 10.2.71.0/24, so the shipped `EPICS_CA_ADDR_LIST=
  10.2.2.255` broadcast never routes to it.
- Falling back to unicast `EPICS_CA_ADDR_LIST=10.2.2.49` **also fails**.
  `mkosioc-lv1` runs two IOCs (`bfo-mk-ioc` pid 25611, `toptica-mk-ioc` pid
  20151) which both bind UDP 5064 with `SO_REUSEADDR`. Broadcast reaches both;
  unicast reaches only one, and it is toptica — which answers
  `CA_PROTO_NOT_FOUND` for every `bfo:` channel.
- Fix: `EPICS_CA_NAME_SERVERS=10.2.2.49:5064` with `EPICS_CA_AUTO_ADDR_LIST=NO`
  and no `EPICS_CA_ADDR_LIST`. TCP name resolution goes to whoever holds TCP
  5064, and that is `bfo-mk-ioc`. Verified: `bfo:mcsStatus` connects, reads 1.

`deploy/tsrs-web.service` now ships this off-VLAN configuration;
`deploy/tsrs-web.container` keeps the on-VLAN broadcast form. Both files
document the other mode.

**Known fragility introduced by this workaround:** TCP 5064 belongs to whichever
IOC starts first. If both restart and toptica wins the race, name resolution
stops silently and the panel goes to NO DATA with nothing in the log. The
durable fix is a pinned `EPICS_CAS_SERVER_PORT` for `bfo-mk-ioc` — an IOC-side
change owned by whoever runs `mkosioc-lv1`. Deploying on a control-VLAN host
removes the problem entirely.

## Compliance findings vs. the 2015 design

Evidence in `reference/gaps.csv` and `reference/plc_summary_bits.csv`.

| # | Finding | State |
|---|---|---|
| 1 | LOTO drill-down showed 4 of 7 feeder bits — an operator could not see three possible causes of `LOTO: Not Ready` | **Fixed** via `compliance_additions.csv` |
| 2 | `Lights OK` (B3/262) and `PLC OK` (B3/267) absent entirely — design defines 13 summary bits, screen implemented 11 | **Fixed** via `compliance_additions.csv` |
| 3 | **No PLC heartbeat bit** | **OPEN** |

On (3): the bit-grouping spreadsheet specifies a "BFO PLC heartbeat bit" but
never assigned it a PLC address, and no such PV appears in the as-built screens.
Without it, a PLC that is alive on the network but no longer scanning cannot be
detected — CA stays connected and the last values persist. The gateway and panel
already implement the check; it only needs `heartbeat_pv` set in
`tsrs.config.json`.

**Do not treat the port as complete until (3) is closed or explicitly accepted.**

Two caption mismatches were flagged by the extractor and both are the *screen*
being more correct than the document (`PLX CB cllosed`, `O/Ls not stripped` —
typos in the 2015 spreadsheet). No action.

## Open items

- [ ] **PLC heartbeat PV address** from a controls engineer — blocks sign-off
- [ ] **Find the summit entrance host.** `hbfbfotsrs-ld1` is the Hilo Base
      Facility copy (REQ-0211). REQ-0210 requires a screen at the *observatory
      entrance*, and `ObsMode.js` in the original workspace is commented "Display
      Observatory Mode on the TSRS Entrance Status Screen" — so a second EL7 host
      probably exists and is the safety-relevant one. Sweep `rpm -q cssapp`
      across display nodes.
- [ ] **Confirm Gemini South scope** — `lhandset.sh` has a live `CP` branch
- [ ] **Verify the five compliance-addition PVs exist on the real IOC.**
      `bfo:cond1bits.B7/.B9/.BA`, `bfo:cond2bits.B2`, `bfo:cond5bits.B4` have
      never been on a screen. If they do not connect, the design specifies them
      but the IOC does not publish them — a display fix becomes an IOC change.
      Set `include_compliance_additions: false` and regenerate meanwhile.
- [ ] **Parallel-run against CS-Studio** for a maintenance period before cutover.
      Both are read-only, so this is free and is the only real correctness check.
- [ ] **Availability model** if the gateway centralises onto a shared Docker VM
- [ ] **`caRepeater` missing from the image** — warning only, but it slows
      reconnect after an IOC restart, since beacons are how a client learns a
      server came back. Confirmed on `mkoswgdkr-lv1`: *"The executable
      caRepeater couldn't be located"*. `--read-only` and `--cap-drop ALL` also
      prevent libca from spawning it. Test IOC-restart recovery deliberately
      rather than discovering the gap during a real outage.
- [ ] **Pin `EPICS_CAS_SERVER_PORT` for `bfo-mk-ioc`** with the `mkosioc-lv1`
      owner, so off-VLAN clients stop depending on a TCP-5064 startup race
- [ ] **`ExecStartPre=docker pull` runs as root** with no GHCR credentials, so
      auto-update on restart does not work

## Verified facts worth keeping

- IOC: `mkosioc-lv1.hi.gemini.edu` = **10.2.2.49**/24 (`ens192`), CA port 5064.
  Runs **two** IOCs under procServ: `bfo-mk-ioc` (procServ telnet port 1237) and
  `toptica-mk-ioc` (1238). `bfo-mk-ioc` holds TCP 5064; both share UDP 5064.
  `dbl` on `bfo-mk-ioc` confirms all `bfo:` summary and `cond{1..7}bits` records
  exist, so the five compliance-addition PVs are fields of records that are
  present and should connect.
- Panel host: `mkoswgdkr-lv1` = **10.2.71.15**/24 (`ens33`) — *not* on the
  control VLAN, which is the root of all the addressing complexity above
- Old EL7 display: `hbfbfotsrs-ld1`, running `css-1-7.el7.gemini` (built 2016) ,
  launched from `/etc/ITOps/tsrs-launcher.sh` (unversioned — get it into git)
- On that host, only `bfo/bfo_overview.opi` had a recent atime; all 12 detail
  screens and `BTO Handset.opi` still showed their May 2018 install date, i.e.
  never opened there
- The wider EL9 problem is bigger than this app: ~60 Gemini `.i686` packages
  (epics-base, edm, and every `-ws` package) have no EL9 path as-is
