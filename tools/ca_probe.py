#!/usr/bin/env python3
"""Ask a host directly whether it serves a Channel Access PV.

Answers the one question that decides a TSRS deployment: can this machine
resolve that channel on that IOC, and if not, at which layer does it fail?
Needs no EPICS install, no root, and no container -- plain stdlib UDP, so it
runs on a bare host before anything else is set up.

    python3 tools/ca_probe.py 10.2.2.49 bfo:mcsStatus

Read the outcome as:

  FOUND        name resolution works. Any remaining failure is app/config, not
               the network.
  NOT_FOUND    the host answered but does not serve that channel. Either the
               PV name is wrong, or -- the usual cause -- the host runs several
               IOCs sharing UDP 5064 and your unicast search reached the wrong
               one. Unicast is delivered to a single socket; broadcast is
               delivered to all. Try --broadcast from the IOC's own subnet, or
               use TCP name resolution (EPICS_CA_NAME_SERVERS).
  NO REPLY     usually routing or firewall. Note the source port printed below
               -- replies return to *that* ephemeral port, not to 5064, so a
               stateless rule pinned to 5064/5065 on both ends drops them; UDP
               needs conntrack/ESTABLISHED. But silence is ambiguous: EPICS base
               IOCs answer NOT_FOUND for an unknown channel, while caproto soft
               IOCs (including sim/tsrs_sim.py) simply say nothing. If a PV you
               know exists on that host returns FOUND, the network is fine and
               the name is wrong.

See "Reaching the IOC" in README.md.
"""
import argparse
import socket
import struct
import sys

CA_VERSION = 13
CA_PROTO_VERSION = 0
CA_PROTO_SEARCH = 6
CA_PROTO_NOT_FOUND = 14
DOREPLY = 10  # ask the server to answer even when it does not have the channel


def probe(host, pv, port=5064, timeout=3.0, broadcast=False):
    name = pv.encode()
    payload = name + b"\0" * ((-len(name) - 1) % 8 + 1)  # pad to multiple of 8
    msg = (struct.pack(">HHHHII", CA_PROTO_VERSION, 0, 0, CA_VERSION, 0, 0)
           + struct.pack(">HHHHII", CA_PROTO_SEARCH, len(payload),
                         DOREPLY, CA_VERSION, 1, 1) + payload)

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if broadcast:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(timeout)
    s.sendto(msg, (host, port))
    print("search for %r sent to %s:%d from %s" % (pv, host, port, s.getsockname()))

    found = refused = False
    try:
        while True:
            data, addr = s.recvfrom(2048)
            off = 0
            while off + 16 <= len(data):
                cmd, psize, dtype, _, _, _ = struct.unpack(">HHHHII", data[off:off + 16])
                if cmd == CA_PROTO_SEARCH:
                    found = True
                    print("FOUND    %s serves %s on TCP port %d" % (addr[0], pv, dtype))
                elif cmd == CA_PROTO_NOT_FOUND:
                    refused = True
                    print("NOT_FOUND %s answered but does not serve %s" % (addr[0], pv))
                off += 16 + psize
            if found:
                break
    except socket.timeout:
        pass

    if found:
        return 0
    if refused:
        print("\nThe host is reachable; the channel is not there. Check the PV "
              "name, or whether several IOCs share UDP %d on that host." % port)
        return 2
    print("\nNO REPLY in %.0fs -- usually routing or firewall; replies return to "
          "the ephemeral source port above, not to %d. Note that some servers "
          "(caproto soft IOCs) stay silent for an unknown channel instead of "
          "answering NOT_FOUND, so retry with a PV you know exists there before "
          "blaming the network." % (timeout, port))
    return 3


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("host", help="IOC address, or a subnet broadcast with --broadcast")
    p.add_argument("pv", nargs="?", default="bfo:mcsStatus",
                   help="channel to search for (default: %(default)s)")
    p.add_argument("--port", type=int, default=5064, help="CA server port")
    p.add_argument("--timeout", type=float, default=3.0)
    p.add_argument("--broadcast", action="store_true",
                   help="send to a broadcast address; reaches every IOC on that subnet")
    a = p.parse_args()
    sys.exit(probe(a.host, a.pv, a.port, a.timeout, a.broadcast))


if __name__ == "__main__":
    main()
