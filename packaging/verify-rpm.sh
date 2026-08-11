#!/bin/bash
# Verify a built tsrs-screen RPM. CI runs exactly this, so "it passed locally"
# means the same thing as "it passed in the pipeline".
#
#   ./packaging/verify-rpm.sh            # verifies the dev build (0.0.0)
#   ./packaging/verify-rpm.sh 1.2.0
#
# Checks, in order of what would actually hurt if it broke:
#   1. the unit is pinned to THIS version's image tag  -- a wrong pin silently
#      runs the wrong code, and nothing on the panel would say so
#   2. /etc/sysconfig/tsrs-web is %config(noreplace)   -- otherwise an upgrade
#      wipes the site's IOC address and the panel goes dark
#   3. an upgrade moves the pin but keeps a hand-edited sysconfig line
#   4. a downgrade rolls the pin back                  -- the rollback story
#   5. the unit parses and the probe runs
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${OUT:-$ROOT/rpmout}"
BUILDER="${BUILDER:-rockylinux:9.3}"

VERSION="${1:-${TSRS_VERSION:-}}"
if [ -z "$VERSION" ]; then
    VERSION="$(git -C "$ROOT" describe --tags --exact-match 2>/dev/null | sed 's/^v//' || true)"
    VERSION="${VERSION:-0.0.0}"
fi

RPM="$OUT/tsrs-screen-$VERSION-1.el9.noarch.rpm"
[ -f "$RPM" ] || { echo "no such RPM: $RPM -- run packaging/build-rpm.sh $VERSION first" >&2; exit 1; }

# The upgrade test needs a second, higher version. Build it from the same
# source so the only difference is the version -- which is exactly the thing
# under test.
NEXT="${VERSION%.*}.$(( ${VERSION##*.} + 1 ))"
if [ ! -f "$OUT/tsrs-screen-$NEXT-1.el9.noarch.rpm" ]; then
    echo "--- building $NEXT for the upgrade test ---"
    "$ROOT/packaging/build-rpm.sh" "$NEXT" >/dev/null
fi

echo "--- verifying $VERSION (upgrade target $NEXT) ---"
docker run --rm --platform linux/amd64 -v "$OUT":/out:ro \
    -e V="$VERSION" -e N="$NEXT" "$BUILDER" bash -euo pipefail -c '
        IMG=ghcr.io/gemini-rtsw/tsrs_screen
        UNIT=/usr/lib/systemd/system/tsrs-web.service

        rpm -i --nodeps "/out/tsrs-screen-$V-1.el9.noarch.rpm"

        echo "[1] image pin"
        grep -qx "Environment=IMAGE=$IMG:$V" "$UNIT" \
            || { echo "FAIL: unit not pinned to $V"; grep "^Environment=IMAGE=" "$UNIT"; exit 1; }

        echo "[2] sysconfig is config(noreplace)"
        rpm -qc tsrs-screen | grep -qx /etc/sysconfig/tsrs-web \
            || { echo "FAIL: sysconfig not marked %config"; exit 1; }

        echo "[3] upgrade keeps site edits, moves the pin"
        echo "EPICS_CA_NAME_SERVERS=10.9.9.9:5064  # SITE EDIT" >> /etc/sysconfig/tsrs-web
        rpm -U --nodeps "/out/tsrs-screen-$N-1.el9.noarch.rpm"
        grep -qx "Environment=IMAGE=$IMG:$N" "$UNIT" \
            || { echo "FAIL: upgrade did not move the pin to $N"; exit 1; }
        grep -q "SITE EDIT" /etc/sysconfig/tsrs-web \
            || { echo "FAIL: upgrade clobbered site config"; exit 1; }

        echo "[4] downgrade rolls the pin back"
        rpm -U --oldpackage --nodeps "/out/tsrs-screen-$V-1.el9.noarch.rpm"
        grep -qx "Environment=IMAGE=$IMG:$V" "$UNIT" \
            || { echo "FAIL: downgrade did not restore $V"; exit 1; }

        echo "[5] unit parses, probe runs"
        # systemd-analyze is not in the minimal builder image. Install it rather
        # than skipping: a check that silently no-ops when its tool is missing
        # is worse than no check, because it reports success.
        command -v systemd-analyze >/dev/null || dnf -y install systemd >/dev/null
        command -v systemd-analyze >/dev/null || { echo "FAIL: no systemd-analyze"; exit 1; }
        # Only real unit errors matter here; the container has no docker binary
        # and no dbus, which systemd-analyze grumbles about either way.
        OUT=$(systemd-analyze verify "$UNIT" 2>&1 || true)
        echo "$OUT" | grep -Ei "unknown lvalue|failed to parse|invalid|bad unit" \
            && { echo "FAIL: unit did not parse cleanly"; exit 1; }
        command -v tsrs-ca-probe >/dev/null || { echo "FAIL: tsrs-ca-probe not on PATH"; exit 1; }
        tsrs-ca-probe --help >/dev/null || { echo "FAIL: tsrs-ca-probe not runnable"; exit 1; }

        echo "ALL CHECKS PASSED"
    '
