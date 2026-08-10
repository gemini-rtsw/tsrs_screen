#!/usr/bin/env python3
"""Soft IOC that serves the TSRS bfo channels for testing.

Serves every channel in gateway/channels.json over real Channel Access, so the
panel and gateway exercise the actual pyepics + CA path rather than a mock.

Channel names contain dots (bfo:cond1bits.B0). On the real system those are
mbbiDirect record *fields*; here they are simply channels whose names happen to
contain a dot, which is transparent to any CA client.

    python3 sim/tsrs_sim.py                 # 8% of bits Not Ready, mode cycling
    python3 sim/tsrs_sim.py --chaos 0       # everything Ready

To test the NO DATA path, stop this process: every indicator must switch to a
hatched NO DATA pill and the panel must raise its banner. If it does not, the
liveness logic is broken -- that is the single most important test here.
"""
import argparse
import itertools
import json
import pathlib

from caproto.server import PVGroup, pvproperty, run

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_CHANNELS = ROOT / "gateway" / "channels.json"

MODE_PVS = ["bfo:summitMode", "bfo:standbyMode", "bfo:baseMode"]


def build_ioc(names, chaos, period):
    """Build a PVGroup subclass carrying one integer channel per name.

    pvproperty descriptors must exist when the metaclass runs, so the channel
    set is assembled with type() rather than attached afterwards.
    """
    attrs = {}
    for i, name in enumerate(names):
        attrs["c%d" % i] = pvproperty(value=1, name=name, dtype=int, doc=name)
    base = type("TSRSChannels", (PVGroup,), attrs)

    class TSRSSim(base):
        driver = pvproperty(value=0, name="sim:tick", dtype=int)

        @driver.startup
        async def driver(self, instance, async_lib):
            cond = [n for n in names if n not in MODE_PVS]
            modes = itertools.cycle(MODE_PVS)
            n_bad = max(0, int(len(cond) * chaos))
            step = 0
            while True:
                # Rotate which bits read Not Ready so every indicator gets
                # exercised over time instead of only a fixed subset.
                bad = set()
                for k in range(n_bad):
                    bad.add(cond[(step * n_bad + k) % len(cond)])
                for name in cond:
                    want = 0 if name in bad else 1
                    ch = self.pvdb[name]
                    if ch.value != want:
                        await ch.write(want)

                # Observatory mode: exactly one of the three reads 1.
                active = next(modes)
                for name in MODE_PVS:
                    if name in self.pvdb:
                        want = 1 if name == active else 0
                        if self.pvdb[name].value != want:
                            await self.pvdb[name].write(want)

                await instance.write(step)
                step += 1
                await async_lib.library.sleep(period)

    return TSRSSim(prefix="")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--channels", type=pathlib.Path, default=DEFAULT_CHANNELS)
    ap.add_argument("--chaos", type=float, default=0.08,
                    help="fraction of bits reading Not Ready (default 0.08)")
    ap.add_argument("--period", type=float, default=6.0,
                    help="seconds between reshuffles (default 6)")
    ap.add_argument("--interface", default="0.0.0.0")
    args = ap.parse_args()

    names = json.loads(args.channels.read_text())
    for m in MODE_PVS:
        if m not in names:
            names.append(m)

    ioc = build_ioc(names, args.chaos, args.period)
    print("TSRS simulator: %d channels on %s (chaos=%.2f period=%.1fs)"
          % (len(names), args.interface, args.chaos, args.period), flush=True)
    run(ioc.pvdb, interfaces=[args.interface])


if __name__ == "__main__":
    main()
