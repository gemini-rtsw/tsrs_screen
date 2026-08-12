#!/usr/bin/env python3
"""Generate the TSRS web panel from the indicator map.

Reads   tsrs_indicators.csv        (as-built)
        compliance_additions.csv   (design-required, absent from as-built)
        tsrs.config.json
Writes  static/index.html, static/style.css, static/app.js
        gateway/channels.json      (the PV list the gateway monitors)

One source of truth: the CSV drives both the panel and the channel list, so the
two can never drift.  Re-run after editing either CSV.

    python3 tools/gen_panel.py
"""
import csv
import hashlib
import html
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"


def read_rows(path, keep_optional):
    if not path.exists():
        return []
    out = []
    with path.open() as fh:
        for line in fh:
            if line.startswith("#") or not line.strip():
                continue
            out.append(line)
    rows = list(csv.DictReader(out))
    if not keep_optional:
        rows = [r for r in rows if r.get("source") != "optional"]
    return rows


def main():
    cfg = json.loads((ROOT / "tsrs.config.json").read_text())

    rows = read_rows(ROOT / "tsrs_indicators.csv", True)
    if cfg.get("include_compliance_additions", True):
        rows += read_rows(ROOT / "compliance_additions.csv",
                          cfg.get("include_optional", False))

    mode_pvs = [pv for _, pv in cfg["mode_pvs"]]

    # Channel list for the gateway: every PV referenced anywhere, deduped.
    channels = sorted({r["pv"] for r in rows} | set(mode_pvs))

    by_screen = {}
    for r in rows:
        by_screen.setdefault(r["screen"], []).append(r)

    # Overview order is explicit in config; detail screens keep their OPI order,
    # summary LED first (it is the screen's own roll-up header).
    order = {pv: i for i, pv in enumerate(cfg["overview_order"])}
    for screen, items in by_screen.items():
        if screen == "overview":
            items.sort(key=lambda r: order.get(r["pv"], 999))
        else:
            items.sort(key=lambda r: (r["kind"] != "summary", int(r["order"])))

    STATIC.mkdir(exist_ok=True)
    (STATIC / "index.html").write_text(render_html(cfg, by_screen))
    (STATIC / "style.css").write_text(STYLE)
    (STATIC / "app.js").write_text(render_js(cfg))

    gw = ROOT / "gateway" / "channels.json"
    gw.parent.mkdir(exist_ok=True)
    gw.write_text(json.dumps(channels, indent=2) + "\n")

    print("wrote %s  (%d screens)" % (STATIC / "index.html", len(by_screen)))
    print("wrote %s  (%d channels)" % (gw, len(channels)))
    props = [r for r in rows if r["source"].startswith("proposed")]
    if props:
        print("  includes %d proposed indicators (compliance additions):" % len(props))
        for r in props:
            print("      %-9s %-20s %s" % (r["screen"], r["pv"], r["label"]))


def led(r):
    """One indicator row: caption + state pill, keyed by PV for JS updates."""
    cls = "ind" + (" ind-summary" if r["kind"] == "summary" else "")
    if r["source"].startswith("proposed"):
        cls += " ind-proposed"
    link = ' data-goto="%s"' % r["links_to"].replace("_detail", "") if r["links_to"] else ""
    title = r["design_description"] or r["label"]
    return (
        '<div class="%s"%s data-pv="%s" title="%s &#10;%s">'
        '<span class="cap">%s</span>'
        '<span class="pill" data-state="init">--</span>'
        "</div>"
    ) % (cls, link, html.escape(r["pv"]),
         html.escape(r["pv"]), html.escape(title),
         html.escape(r["label"] or r["pv"]))


def asset_version(text):
    """Content hash for cache-busting.

    The panel is a wall display: nobody hard-refreshes it, and a kiosk browser
    will happily serve a cached style.css or app.js for weeks after a deploy.
    That is how a fixed NO DATA banner stayed on screen after the fix shipped.
    Hashing the content means the URL changes if and only if the asset does, so
    a `docker pull` + restart is genuinely all a deploy needs -- and the drift
    check stays deterministic, unlike a timestamp or build number.
    """
    return hashlib.sha256(text.encode()).hexdigest()[:8]


def render_html(cfg, by_screen):
    parts = []
    for screen, items in sorted(by_screen.items(), key=lambda kv: kv[0] != "overview"):
        title = cfg["screen_titles"].get(screen, screen)
        back = "" if screen == "overview" else \
            '<button class="back" data-goto="overview">&larr; Overview</button>'
        grid = "grid grid-3" if screen == "overview" else "grid grid-2"
        body = "\n".join(led(r) for r in items)
        mode = MODE_BLOCK if screen == "overview" else ""
        parts.append(
            '<section class="screen" id="scr-%s" hidden>\n'
            '  <header class="scr-head">%s<h2>%s</h2></header>\n'
            '  <div class="%s">\n%s\n  </div>\n%s'
            "</section>" % (screen, back, html.escape(title), grid, body, mode))

    return TEMPLATE.format(
        title=html.escape(cfg["title"]),
        subtitle=html.escape(cfg.get("subtitle", "")),
        screens="\n".join(parts),
        cssver=asset_version(STYLE),
        jsver=asset_version(render_js(cfg)),
    )


MODE_BLOCK = """  <div class="mode">
    <div class="mode-cap">Observatory Mode:</div>
    <div class="mode-val" id="obsmode">--</div>
  </div>
"""

TEMPLATE = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<link rel="stylesheet" href="style.css?v={cssver}">
<body>
<div id="linkdown" class="linkdown" hidden>
  <strong>NO DATA FROM GATEWAY</strong>
  <span id="linkdown-detail"></span>
</div>
<main>
{screens}
</main>
<footer>
  <span id="conn" class="conn">connecting&hellip;</span>
  <span class="sep">|</span>
  <span>{subtitle}</span>
  <span class="sep">|</span>
  <span id="stamp"></span>
</footer>
<script src="app.js?v={jsver}"></script>
</body>
"""

STYLE = """/* TSRS panel. Light palette deliberately mirrors the approved 2016 CS-Studio
   screen -- this display is the REQ-TSRS-0211 copy of the entrance screen, so
   visual fidelity to the reviewed design matters more than house style. */
:root {
  --bg: #eef0fa;
  --fg: #10131c;
  --panel: #ffffff;
  --line: #b9bed4;
  --ok-1: #4cff4c; --ok-2: #00b800; --ok-fg: #062b06;
  --bad-1: #ff8a8a; --bad-2: #e00000; --bad-fg: #2b0606;
  --stale: #9aa0b4; --stale-fg: #23262f;
  --warn: #b35c00;

  /* Summit wash. ONLY Summit is coloured: it is the state that matters, and
     once the summit crew has left the mountain it cannot easily be changed, so
     the screen must make it unmistakable from across the room. Colouring the
     other modes too would dilute exactly that signal.
     Still light enough for black body text (~7:1), and the white indicator
     cards keep the Ready / Not Ready pills clearly separated from it. */
  --bg-summit: #d78f8f;
  --edge-summit: #7a1f1f;
}
* { box-sizing: border-box; }

/* Must come before any rule that sets `display`, and must stay !important.
   Visibility of the NO DATA banner and of all 12 screens is driven purely by
   the `hidden` attribute from app.js. An author `display` declaration beats the
   UA stylesheet's `[hidden] { display: none }` no matter the specificity, so
   without this guard `.linkdown { display: flex }` pins the red banner on
   screen forever -- including when the gateway is perfectly healthy. That is
   the worst possible failure for an annunciator: it trains operators to ignore
   the one alarm the display exists to raise. */
[hidden] { display: none !important; }
html, body { margin: 0; height: 100%; }
body {
  background: var(--bg); color: var(--fg);
  font: 16px/1.3 "Helvetica Neue", Arial, sans-serif;
  display: flex; flex-direction: column;
}
main { flex: 1; padding: 1.2rem 1.4rem; overflow-y: auto; }

/* Mode tint. Transition is slow on purpose -- an abrupt full-screen flip in
   peripheral vision reads as an alarm, and this is a state, not an event. */
body { transition: background-color .6s ease; }
body[data-mode="summit"] { background: var(--bg-summit); }
body[data-mode="summit"] main { border-top: 3px solid var(--edge-summit); }
body[data-mode="summit"] .mode-val { color: var(--edge-summit); }

.linkdown {
  background: #b00000; color: #fff; padding: .65rem 1rem;
  font-weight: 700; letter-spacing: .04em; text-align: center;
  display: flex; gap: 1rem; justify-content: center; align-items: baseline;
}
.linkdown span { font-weight: 400; opacity: .9; font-size: .85em; }

.scr-head { display: flex; align-items: center; gap: 1rem; margin: 0 0 1.1rem; }
.scr-head h2 { font-size: 1.15rem; margin: 0; font-weight: 700; }
.back {
  font: inherit; padding: .35rem .7rem; cursor: pointer;
  background: var(--panel); border: 1px solid var(--line); border-radius: 5px;
}
.back:hover { background: #dfe3f2; }

.grid { display: grid; gap: .55rem 1.6rem; }
.grid-3 { grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); }
.grid-2 { grid-template-columns: repeat(auto-fit, minmax(430px, 1fr)); }

.ind {
  display: grid; grid-template-columns: 1fr 190px;
  align-items: center; gap: .8rem; padding: .3rem 0;
}
.ind[data-goto] { cursor: pointer; }
.ind[data-goto]:hover .cap { text-decoration: underline; }
.cap { text-align: right; font-weight: 700; }
.grid-2 .cap { text-align: left; font-weight: 400; }
.ind-summary .cap { font-weight: 700; }

.pill {
  border: 1px solid #6d7288; border-radius: 3px;
  padding: .42rem .5rem; text-align: center;
  font-weight: 700; font-size: .95rem;
  background: linear-gradient(180deg, var(--stale) 0%, #6f7488 100%);
  color: var(--stale-fg);
}
.pill[data-state="1"] {
  background: linear-gradient(180deg, var(--ok-1) 0%, var(--ok-2) 100%);
  color: var(--ok-fg);
}
.pill[data-state="0"] {
  background: linear-gradient(180deg, var(--bad-1) 0%, var(--bad-2) 100%);
  color: var(--bad-fg);
}
/* Stale / disconnected must never be mistakable for a healthy reading. */
.pill[data-state="stale"], .pill[data-state="init"] {
  background: repeating-linear-gradient(45deg,
    #8b90a4 0 7px, #767b90 7px 14px);
  color: #fff; border-color: #4d5163;
}
.pill[data-alarm="1"] { outline: 2px solid var(--warn); outline-offset: 1px; }

.ind-proposed .cap::after {
  content: " \\25B8"; color: var(--warn); font-weight: 700;
}

.mode { margin: 2rem 0 .5rem; text-align: center; }
.mode-cap { font-weight: 700; font-size: 1.05rem; }
.mode-val {
  font-size: clamp(3rem, 11vw, 7.5rem); font-weight: 700; line-height: 1.05;
}
.mode-val[data-state="stale"] { color: var(--stale); }

footer {
  border-top: 1px solid var(--line); background: var(--panel);
  padding: .45rem 1rem; font-size: .82rem; display: flex; gap: .7rem;
  align-items: center; color: #4a4f63;
}
.sep { opacity: .4; }
.conn::before {
  content: "\\25CF "; color: var(--stale);
}
.conn[data-up="1"]::before { color: var(--ok-2); }
.conn[data-up="0"]::before { color: #e00000; }
"""


def render_js(cfg):
    return JS.replace("__POLL_MS__", str(int(1000 / cfg["poll_hz"]))) \
             .replace("__STALE_S__", str(cfg["stale_after_s"])) \
             .replace("__MODE_PVS__", json.dumps(cfg["mode_pvs"]))


# NOTE ON LIVENESS -- read before changing the freshness logic below.
#
# Channel Access monitors fire only when a value CHANGES. A status bit that has
# read "Ready" for three days generates no updates at all, so "time since last
# update" is NOT a staleness signal -- it would grey out every healthy channel.
#
# Liveness therefore comes from three independent layers:
#   1. CA connection state (per channel). libca detects a dead IOC via TCP plus
#      server beacons and flips the connection callback. This is authoritative
#      for "is the IOC talking to us".
#   2. Gateway reachability (whole panel). A failed fetch blanks everything.
#   3. PLC heartbeat (optional, not yet wired). The 2015 design specifies a
#      "BFO PLC heartbeat bit" but gives no address, and no such PV appears in
#      the as-built screens. Set "heartbeat_pv" in tsrs.config.json once the
#      controls engineer identifies it; the gateway will then report staleness
#      and the panel raises a banner. Until then, a PLC that is alive on the
#      network but no longer scanning cannot be detected here -- that is a real
#      residual gap, documented in README.md, not an oversight.


JS = """'use strict';
// TSRS panel client. Polls the gateway; the gateway holds live CA monitors.
// Display latency = poll interval; acquisition stays event-driven upstream.
const POLL_MS = __POLL_MS__;
const STALE_S = __STALE_S__;
const MODE_PVS = __MODE_PVS__;

const inds = new Map();
document.querySelectorAll('.ind').forEach(el => {
  const pv = el.dataset.pv;
  if (!inds.has(pv)) inds.set(pv, []);
  inds.get(pv).push(el.querySelector('.pill'));
});

// ---- navigation -------------------------------------------------------------
function show(screen) {
  document.querySelectorAll('.screen').forEach(s => { s.hidden = true; });
  const el = document.getElementById('scr-' + screen);
  (el || document.getElementById('scr-overview')).hidden = false;
  if (location.hash !== '#' + screen) history.replaceState(null, '', '#' + screen);
  window.scrollTo(0, 0);
}
document.addEventListener('click', e => {
  const t = e.target.closest('[data-goto]');
  if (t) show(t.dataset.goto);
});
window.addEventListener('hashchange', () => show(location.hash.slice(1) || 'overview'));
show(location.hash.slice(1) || 'overview');

// ---- rendering --------------------------------------------------------------
function paint(pill, state, alarm) {
  pill.dataset.state = state;
  pill.dataset.alarm = alarm ? '1' : '0';
  pill.textContent = state === '1' ? 'Ready'
                   : state === '0' ? 'Not Ready'
                   : 'NO DATA';
}

function setLinkDown(down, detail) {
  document.getElementById('linkdown').hidden = !down;
  document.getElementById('linkdown-detail').textContent = detail || '';
  const c = document.getElementById('conn');
  c.dataset.up = down ? '0' : '1';
  c.textContent = down ? 'gateway unreachable' : 'gateway ok';
}

function allStale(detail) {
  setLinkDown(true, detail);
  inds.forEach(pills => pills.forEach(p => paint(p, 'stale', false)));
  const m = document.getElementById('obsmode');
  m.textContent = 'NO DATA';
  m.dataset.state = 'stale';
  document.body.dataset.mode = '';
}

async function tick() {
  let data;
  try {
    const r = await fetch('api/status', { cache: 'no-store' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    data = await r.json();
  } catch (err) {
    allStale(String(err.message || err));
    return;
  }

  const ch = data.channels || {};
  let live = 0, total = 0;

  inds.forEach((pills, pv) => {
    total++;
    const c = ch[pv];
    // Liveness = CA connection state, NOT update age. CA monitors fire only on
    // change, so a stable bit legitimately reports no updates for days.
    const fresh = !!(c && c.connected && c.value !== null && c.value !== undefined);
    let state = 'stale';
    if (fresh) {
      state = Number(c.value) ? '1' : '0';
      live++;
    }
    pills.forEach(p => {
      paint(p, state, fresh && c.severity);
      p.title = c ? (pv + '\\nupdated ' + (data.now - c.ts).toFixed(0) + 's ago')
                  : (pv + '\\nnot monitored');
    });
  });

  // Observatory Mode: first mode PV reading 1 wins, matching ObsMode.js.
  const m = document.getElementById('obsmode');
  let mode = null, modeFresh = false;
  for (const [name, pv] of MODE_PVS) {
    const c = ch[pv];
    if (c && c.connected) {
      modeFresh = true;
      if (Number(c.value) === 1) { mode = name; break; }
    }
  }
  m.textContent = !modeFresh ? 'NO DATA' : (mode || 'Unknown');
  m.dataset.state = modeFresh ? 'ok' : 'stale';

  // Whole-screen tint for Observatory Mode, so the mode is readable from
  // across the room without reading anything. Only set when the mode is
  // actually known: a comms failure must never be able to imply a mode.
  document.body.dataset.mode = modeFresh && mode ? mode.toLowerCase() : '';

  // Optional PLC heartbeat (see NOTE ON LIVENESS). Absent until configured.
  const hb = data.heartbeat;
  if (hb && hb.configured && !hb.ok) {
    setLinkDown(true, 'PLC heartbeat stale (' + hb.age.toFixed(0) + 's) \\u2014 '
                      + 'values below may not reflect the plant');
  } else if (live === 0 && total > 0) {
    setLinkDown(true, 'gateway reachable but no channel is connected');
  } else {
    setLinkDown(false);
  }

  document.getElementById('conn').textContent =
    live + '/' + total + ' channels connected';
  document.getElementById('conn').dataset.up = (live === total) ? '1' : '0';
  document.getElementById('stamp').textContent =
    new Date(data.now * 1000).toLocaleTimeString();
}

tick();
setInterval(tick, POLL_MS);
"""


if __name__ == "__main__":
    main()
