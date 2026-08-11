#!/bin/bash
# Release from a laptop: the same steps as .github/workflows/release.yml, in the
# same order, using the same build/verify scripts.
#
#   ./packaging/release.sh --dry-run    # show what would happen, change nothing
#   ./packaging/release.sh              # image, RPM, upload, git tag
#   ./packaging/release.sh --no-image   # RPM only (image already published)
#
# Version comes from %global specver in packaging/tsrs-screen.spec -- never a
# command-line argument, so the local path cannot release a version the repo
# does not claim.
#
# Needs: docker logged in to ghcr.io, a checkout of gemini-rtsw-repo (defaults
# to ../gemini-rtsw-repo, override with RTSW_REPO), and optionally `gh` to kick
# the :latest rebuild.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RTSW_REPO="${RTSW_REPO:-$ROOT/../gemini-rtsw-repo}"
IMAGE="${IMAGE:-ghcr.io/gemini-rtsw/tsrs_screen}"

DRY=0; DO_IMAGE=1
for a in "$@"; do
    case "$a" in
        --dry-run) DRY=1 ;;
        --no-image) DO_IMAGE=0 ;;
        *) echo "unknown option: $a" >&2; exit 1 ;;
    esac
done
run() { if [ "$DRY" = 1 ]; then echo "  would: $*"; else "$@"; fi; }

V=$(awk '/^%global specver/ {print $3}' "$ROOT/packaging/tsrs-screen.spec")
[ -n "$V" ] || { echo "no specver in packaging/tsrs-screen.spec" >&2; exit 1; }
echo "$V" | grep -Eq '^[0-9]+(\.[0-9]+)*$' \
    || { echo "specver '$V' must be digits and dots (rpm uses '-' as the version/release separator)" >&2; exit 1; }

# Same guards the workflow applies, checked before anything is built or pushed.
if git -C "$ROOT" rev-parse -q --verify "refs/tags/v$V" >/dev/null; then
    echo "v$V is already released -- bump specver" >&2; exit 1
fi
if [ -n "$(git -C "$ROOT" status --porcelain)" ]; then
    echo "working tree is dirty -- commit first, or the tag will not describe what shipped" >&2; exit 1
fi
if [ ! -x "$RTSW_REPO/upload-rpm.sh" ]; then
    echo "no upload-rpm.sh at $RTSW_REPO (set RTSW_REPO)" >&2; exit 1
fi

echo "releasing $V${DRY:+ (dry run)}"

if [ "$DO_IMAGE" = 1 ]; then
    echo "1. image"
    # --platform matches the Dockerfile and the runner: pyepics ships an x86-64
    # libca, so an arm64 image would start and die on the first CA call.
    run docker build --platform linux/amd64 -t "$IMAGE:$V" -t "$IMAGE:latest" "$ROOT"
    run docker push "$IMAGE:$V"
    run docker push "$IMAGE:latest"
else
    echo "1. image  -- skipped (--no-image)"
fi

echo "2. rpm"
# Built by the shared pipeline script, exactly as CI does it.
run "$ROOT/gemini-rtsw-ci/build_rpm.sh" --el 9 --profile lightweight \
    --spec packaging/tsrs-screen.spec
run env OUT="$ROOT/rpms" "$ROOT/packaging/verify-rpm.sh"

echo "3. register with gemini-rtsw-repo"
# --tag-only, then let the repo's own runner rebuild :latest -- that image is
# multi-GB and the rebuild is a read-modify-write on one shared tag.
run "$RTSW_REPO/upload-rpm.sh" --tag-only "$ROOT/rpms/tsrs-screen-$V-1.el9.noarch.rpm"
if command -v gh >/dev/null; then
    run gh workflow run rebuild-latest -R gemini-rtsw/gemini-rtsw-repo
else
    echo "  no gh CLI -- publish :latest by hand:"
    echo "    Actions -> rebuild-latest -> Run workflow (gemini-rtsw/gemini-rtsw-repo)"
fi

echo "4. tag"
# Last, so a tag always means "this shipped", not "this was attempted".
run git -C "$ROOT" tag -a "v$V" -m "Release v$V"
run git -C "$ROOT" push origin "v$V"

echo "done: $V"
[ "$DRY" = 1 ] || echo "note: pushing v$V also triggers the release workflow, which republishes the same version -- harmless, but expect a second run"
