#!/bin/bash
# Build the tsrs-screen RPM. THIS is the build -- CI calls this script, it does
# not reimplement it, so a local run and a pipeline run are the same code path.
#
#   ./packaging/build-rpm.sh            # dev build, version 0.0.0 (or the exact git tag)
#   ./packaging/build-rpm.sh 1.2.0      # explicit version
#
# Output: rpmout/tsrs-screen-<version>-1.el9.noarch.rpm
#
# Runs in a container so the result does not depend on the host: EL9 macros and
# rpmbuild come from the builder image, and --platform pins the arch so an
# Apple Silicon laptop produces the same package as the x86_64 runner. The RPM
# is noarch, but the *build* still needs a consistent el9 dist tag.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${OUT:-$ROOT/rpmout}"

# Pin the builder. `rockylinux:9` moves, and a moving builder is how a package
# that built last month stops building today for reasons nobody can reproduce.
BUILDER="${BUILDER:-rockylinux:9.3}"

VERSION="${1:-${TSRS_VERSION:-}}"
if [ -z "$VERSION" ]; then
    # An exact tag means a release build; anything else is a dev build and must
    # NOT claim a real version -- the version is the image tag, and pretending
    # to be 1.2.0 would pin the unit to an image that does not exist.
    VERSION="$(git -C "$ROOT" describe --tags --exact-match 2>/dev/null | sed 's/^v//' || true)"
    VERSION="${VERSION:-0.0.0}"
fi

echo "building tsrs-screen $VERSION (builder: $BUILDER)"
mkdir -p "$OUT"

docker run --rm --platform linux/amd64 \
    -v "$ROOT":/src:ro -v "$OUT":/out -e V="$VERSION" "$BUILDER" \
    bash -euo pipefail -c '
        dnf -y install rpm-build systemd-rpm-macros tar >/dev/null
        mkdir -p /root/rpmbuild/SOURCES
        cd /src
        # Only the packaged inputs go into the tarball -- keeps the source RPM
        # honest about what the package is actually built from.
        tar czf "/root/rpmbuild/SOURCES/tsrs-screen-$V.tar.gz" \
            --transform "s,^,tsrs-screen-$V/," \
            deploy/tsrs-web.service.in deploy/tsrs-web.sysconfig tools/ca_probe.py
        rpmbuild -bb --define "_version $V" /src/packaging/tsrs-screen.spec
        cp /root/rpmbuild/RPMS/noarch/*.rpm /out/
    '

ls -l "$OUT"/tsrs-screen-"$VERSION"-*.rpm
