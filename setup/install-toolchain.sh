#!/usr/bin/env bash
# Copyright (c) 2026 Dirk Eibach
# SPDX-License-Identifier: BSD-2-Clause
# Reproduces the user-space setup: OSS CAD Suite + Python venv with Amaranth.
# Usage: setup/install-toolchain.sh [oss-cad-suite release date, e.g. 2026-10-30]
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
rel="${1:-2026-10-30}"   # the build that packs the design; later ones fail, see docs/toolchain-notes.md
tag="${rel//-/}"

if [ ! -d "$root/toolchain/oss-cad-suite" ]; then
    mkdir -p "$root/toolchain/dl"
    url="https://github.com/YosysHQ/oss-cad-suite-build/releases/download/${rel}/oss-cad-suite-linux-x64-${tag}.tgz"
    curl -fSL -o "$root/toolchain/dl/oss-cad-suite.tgz" "$url"
    tar -xzf "$root/toolchain/dl/oss-cad-suite.tgz" -C "$root/toolchain"
    rm "$root/toolchain/dl/oss-cad-suite.tgz"
fi

# Dev headers/libs for LiteX's Verilator sim modules, unpacked locally (no root, no install).
if [ ! -d "$root/toolchain/sysroot/usr/include/event2" ]; then
    mkdir -p "$root/toolchain/sysroot/debs"
    (cd "$root/toolchain/sysroot/debs" && apt-get download libevent-dev libjson-c-dev libjson-c5 \
        libevent-2.1-7t64 libevent-core-2.1-7t64 libevent-extra-2.1-7t64 \
        libevent-pthreads-2.1-7t64 libevent-openssl-2.1-7t64 && \
        for d in *.deb; do dpkg -x "$d" ..; done)
fi

if [ ! -d "$root/.venv" ]; then
    python3 -m venv "$root/.venv"
fi
"$root/.venv/bin/pip" install -q --upgrade pip
"$root/.venv/bin/pip" install -q -r "$root/requirements.txt"
echo "done. use: source env.sh"
