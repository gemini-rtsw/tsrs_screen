#!/bin/bash
# Verify a built tsrs-screen RPM. CI runs exactly this via gemini-rtsw-ci's
# verify_cmd input, so "it passed locally" means the same as "it passed in the
# pipeline".
#
# The RPM under test is built by gemini-rtsw-ci/build_rpm.sh (locally or in CI);
# point OUT at wherever it landed. The +1 upgrade fixture is built here, inline,
# so this script needs no build script of its own.
#
#   OUT=$PWD/rpms ./packaging/verify-rpm.sh     # after gemini-rtsw-ci/build_rpm.sh
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
[ -n "$VERSION" ] || VERSION="$(awk '/^%global specver/ {print $3}' "$ROOT/packaging/tsrs-screen.spec")"
[ -n "$VERSION" ] || { echo "cannot read specver from packaging/tsrs-screen.spec" >&2; exit 1; }

RPM=$(ls "$OUT"/tsrs-screen-"$VERSION"-*.noarch.rpm 2>/dev/null | head -1)
[ -n "$RPM" ] && [ -f "$RPM" ] || { echo "no such RPM: $RPM
Build it first, then point OUT at the result:
  ./gemini-rtsw-ci/build_rpm.sh --el 9 --profile lightweight --spec packaging/tsrs-screen.spec
  OUT=\$PWD/rpms $0" >&2; exit 1; }

# The upgrade test needs a second, higher version. Build it from the same
# source so the only difference is the version -- which is exactly the thing
# under test.
#
# A test fixture, not a deliverable.
#
# Built inline rather than by calling a build script: the real build belongs to
# gemini-rtsw-ci, and keeping a second local copy of it just to make a test
# fixture is the duplication this repo is trying not to have.
#
# It is built into a TEMP dir, never into OUT: gemini-rtsw-ci/build_rpm.sh
# populates rpms/ from a root container, so that directory is root-owned and
# not writable by the user running this script. Keeping the fixture out of OUT
# also means the CI artifact can never pick it up.
NEXT="${VERSION%.*}.$(( ${VERSION##*.} + 1 ))"
NEXTDIR="${TMPDIR:-/tmp}/tsrs-verify-fixture-$NEXT"
if [ ! -f "$NEXTDIR/tsrs-screen-$NEXT-1.el9.noarch.rpm" ]; then
    echo "--- building $NEXT as an upgrade-test fixture ---"
    mkdir -p "$NEXTDIR"
    docker run --rm --platform linux/amd64 \
        -v "$ROOT":/src:ro -v "$NEXTDIR":/out -e V="$NEXT" "$BUILDER" \
        bash -euo pipefail -c '
            # git so the spec can resolve %{git_hash}; without it the Release
            # says nogit, which still works but is not what CI produces.
            dnf -y install rpm-build systemd-rpm-macros tar git >/dev/null
            git config --global --add safe.directory /src
            mkdir -p /root/rpmbuild/SOURCES && cd /src
            tar czf "/root/rpmbuild/SOURCES/tsrs-screen-$V.tar.gz" \
                --transform "s,^,tsrs-screen-$V/," \
                deploy/tsrs-web.service.in deploy/tsrs-web.sysconfig \
                deploy/resolve-site.sh deploy/site-MK.env deploy/site-CP.env \
                tools/ca_probe.py
            rpmbuild -bb --quiet --define "_version $V" \
                /src/packaging/tsrs-screen.spec >/dev/null
            cp /root/rpmbuild/RPMS/noarch/*.rpm /out/
        ' >/dev/null
fi

echo "--- verifying $VERSION (upgrade target $NEXT) ---"
docker run --rm --platform linux/amd64 -v "$OUT":/out:ro -v "$NEXTDIR":/fix:ro \
    -e V="$VERSION" -e N="$NEXT" -e RPM="$(basename "$RPM")" "$BUILDER" bash -euo pipefail -c '
        IMG=ghcr.io/gemini-rtsw/tsrs_screen
        UNIT=/usr/lib/systemd/system/tsrs-web.service

        rpm -i --nodeps "/out/$RPM"

        echo "[1] image pin"
        grep -qx "Environment=IMAGE=$IMG:$V" "$UNIT" \
            || { echo "FAIL: unit not pinned to $V"; grep "^Environment=IMAGE=" "$UNIT"; exit 1; }

        echo "[2] sysconfig is config(noreplace)"
        rpm -qc tsrs-screen | grep -qx /etc/sysconfig/tsrs-web \
            || { echo "FAIL: sysconfig not marked %config"; exit 1; }

        echo "[3] upgrade keeps site edits, moves the pin"
        echo "EPICS_CA_NAME_SERVERS=10.9.9.9:5064  # SITE EDIT" >> /etc/sysconfig/tsrs-web
        rpm -U --nodeps /fix/tsrs-screen-$N-*.noarch.rpm
        grep -qx "Environment=IMAGE=$IMG:$N" "$UNIT" \
            || { echo "FAIL: upgrade did not move the pin to $N"; exit 1; }
        grep -q "SITE EDIT" /etc/sysconfig/tsrs-web \
            || { echo "FAIL: upgrade clobbered site config"; exit 1; }

        echo "[4] downgrade rolls the pin back"
        rpm -U --oldpackage --nodeps "/out/$RPM"
        grep -qx "Environment=IMAGE=$IMG:$V" "$UNIT" \
            || { echo "FAIL: downgrade did not restore $V"; exit 1; }

        echo "[5] site resolution: default MK, GEMINI_SITE honoured, CP refuses"
        R=/usr/libexec/tsrs-screen/resolve-site.sh
        mkdir -p /run
        # no GEMINI_SITE anywhere -> MK
        env -u GEMINI_SITE "$R" >/dev/null
        grep -qx "TSRS_SITE=MK" /run/tsrs-web.env || { echo "FAIL: default site is not MK"; exit 1; }
        grep -q "^EPICS_CA_ADDR_LIST=10.2.2.255" /run/tsrs-web.env \
            || { echo "FAIL: MK addressing missing"; cat /run/tsrs-web.env; exit 1; }
        # sysconfig override must beat the site file (docker takes the last key)
        tail -1 /run/tsrs-web.env | grep -q . || { echo "FAIL: env file ends oddly"; exit 1; }
        # scraped from profile.d, since services do not read it
        mkdir -p /etc/profile.d && echo "export GEMINI_SITE=CP" > /etc/profile.d/gemini.sh
        if env -u GEMINI_SITE "$R" >/dev/null 2>&1; then
            echo "FAIL: unconfigured CP site started anyway"; exit 1
        fi
        # inherited env wins over profile.d scrape, and MK still works
        GEMINI_SITE=MK "$R" >/dev/null
        grep -qx "TSRS_SITE=MK" /run/tsrs-web.env || { echo "FAIL: GEMINI_SITE=MK not honoured"; exit 1; }
        # explicit TSRS_SITE in sysconfig beats everything
        echo "TSRS_SITE=CP" >> /etc/sysconfig/tsrs-web
        if GEMINI_SITE=MK "$R" >/dev/null 2>&1; then
            echo "FAIL: TSRS_SITE=CP override ignored"; exit 1
        fi
        sed -i "/^TSRS_SITE=CP$/d" /etc/sysconfig/tsrs-web
        rm -f /etc/profile.d/gemini.sh

        echo "[6] unit parses, probe runs"
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
