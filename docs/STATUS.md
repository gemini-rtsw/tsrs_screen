# Status — 2026-08-10

Current state and open items. `CLAUDE.md` holds the durable invariants; this file
changes as work progresses.

## Where things stand

**Working end to end against the simulator.** 76/76 channels connected,
disconnect detected in ~1 s, native reconnect ~6 s, watchdog rebuild verified.

**CI green path:** drift check → CA smoke test (pyepics on the x86_64 runner) →
publish to `ghcr.io/gemini-rtsw/tsrs_screen` (amd64).

**Deployed to `mkoswgdkr-lv1` (10.2.71.15)** via a systemd unit on port 8090. The
gateway runs and serves, but `ca_connected: 0` — the host cannot reach the IOC:

```
$ nc -zv 10.2.2.49 5064
Ncat: No route to host.
```

`mkoswgdkr-lv1` is a general Docker host outside the control VLAN. A firewall
request is in flight for TCP/UDP 5064–5065 to `mkosioc-lv1` (10.2.2.49). **Not
required if deployed on a control-VLAN host** — which is where this ends up
anyway.

The application path is proven on that host regardless: a foreground run loaded
pyepics and subscribed to all 76 channels successfully.

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
      server came back
- [ ] **`ExecStartPre=docker pull` runs as root** with no GHCR credentials, so
      auto-update on restart does not work

## Verified facts worth keeping

- IOC: `mkosioc-lv1.hi.gemini.edu` = **10.2.2.49**, CA port 5064
- Old EL7 display: `hbfbfotsrs-ld1`, running `css-1-7.el7.gemini` (built 2016) ,
  launched from `/etc/ITOps/tsrs-launcher.sh` (unversioned — get it into git)
- On that host, only `bfo/bfo_overview.opi` had a recent atime; all 12 detail
  screens and `BTO Handset.opi` still showed their May 2018 install date, i.e.
  never opened there
- The wider EL9 problem is bigger than this app: ~60 Gemini `.i686` packages
  (epics-base, edm, and every `-ws` package) have no EL9 path as-is
