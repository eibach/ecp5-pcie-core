#!/usr/bin/env bash
# Copyright (c) 2026 Dirk Eibach
# SPDX-License-Identifier: BSD-2-Clause
# Builds the Versa image with the pinned dependencies: LitePCIe bench/soft/versa.py, the options of the tested bitstream.
# Usage: setup/build.sh [extra versa.py options]      output: build/versa/{gateware,software,driver,csr.csv}
# Add --seed N to try another placement. Without --build the script only generates the Verilog (a quick check of the setup).
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
source "$root/env.sh"
cd "$root"
args=(--analog wizard --with-analyzer --ltssm-autostart --flash-freq 62.0 --driver --output-dir build/versa)
if [ "${1:-}" = "--generate-only" ]; then shift; else args+=(--build); fi
exec python3 deps/litepcie/bench/soft/versa.py "${args[@]}" "$@"
