# Copyright (c) 2026 Dirk Eibach
# SPDX-License-Identifier: BSD-2-Clause
# Source this file:  source env.sh
# Puts the OSS CAD Suite (yosys, nextpnr-ecp5, prjtrellis, openFPGALoader, iverilog,
# verilator, gtkwave) and the Amaranth virtualenv on PATH. No root needed.
_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$_here/toolchain/oss-cad-suite/environment" ]; then
    source "$_here/toolchain/oss-cad-suite/environment"
else
    echo "env.sh: toolchain/oss-cad-suite missing, run ./setup/install-toolchain.sh" >&2
fi
source "$_here/.venv/bin/activate"
# Local sysroot with libevent/json-c dev files (needed by LiteX's Verilator sim modules; no root).
# Created by scripts/install-toolchain.sh from Ubuntu .debs unpacked with dpkg -x.
_sr="$_here/toolchain/sysroot"
if [ -d "$_sr/usr/include" ]; then
    export CPATH="$_sr/usr/include${CPATH:+:$CPATH}"
    export LIBRARY_PATH="$_sr/usr/lib/x86_64-linux-gnu${LIBRARY_PATH:+:$LIBRARY_PATH}"
    export LD_LIBRARY_PATH="$_sr/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
unset _sr
export PROJECT_ROOT="$_here"
unset _here
