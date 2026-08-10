'use strict';
// TSRS panel client. Polls the gateway; the gateway holds live CA monitors.
// Display latency = poll interval; acquisition stays event-driven upstream.
const POLL_MS = 500;
const STALE_S = 5.0;
const MODE_PVS = [["Summit", "bfo:summitMode"], ["Standby", "bfo:standbyMode"], ["Base", "bfo:baseMode"]];

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
      p.title = c ? (pv + '\nupdated ' + (data.now - c.ts).toFixed(0) + 's ago')
                  : (pv + '\nnot monitored');
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

  // Optional PLC heartbeat (see NOTE ON LIVENESS). Absent until configured.
  const hb = data.heartbeat;
  if (hb && hb.configured && !hb.ok) {
    setLinkDown(true, 'PLC heartbeat stale (' + hb.age.toFixed(0) + 's) \u2014 '
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
