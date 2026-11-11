#!/usr/bin/env bash
# Fetches the pinned LiteX, LitePCIe and LiteICLink (setup/versions.lock) into deps/ and installs them into the venv.
# Run setup/install-toolchain.sh first (OSS CAD Suite and the venv). No root needed.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$root/deps"
while read -r name url sha _; do
    case "$name" in ''|'#'*) continue ;; esac
    if [ ! -d "$root/deps/$name/.git" ]; then
        git clone -q "$url" "$root/deps/$name"
    fi
    git -C "$root/deps/$name" fetch -q origin 2>/dev/null || true
    git -C "$root/deps/$name" checkout -q "$sha"
    echo "$name  $(git -C "$root/deps/$name" rev-parse --short HEAD)"
done < "$root/setup/versions.lock"
pip="$root/.venv/bin/pip"
[ -x "$pip" ] || { echo "no .venv: run setup/install-toolchain.sh first" >&2; exit 1; }
"$pip" install -q pyyaml requests packaging pexpect pytest
for name in migen litex litex-boards litescope liteiclink litepcie; do
    "$pip" install -q --no-deps -e "$root/deps/$name"
done
echo "done. use: source env.sh && setup/build.sh"
