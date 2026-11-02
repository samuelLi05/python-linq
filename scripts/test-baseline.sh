#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$project_root/scripts/common.sh"

if ! interpreter="$(find_debug_interpreter "$project_root/build/debug")"; then
    printf 'Debug interpreter is not built. Run bash scripts/setup.sh first.\n' >&2
    exit 1
fi

mkdir -p "$project_root/logs"
cd "$project_root/build/debug"
"$interpreter" -m test -j2 test_grammar test_ast test_compile test_symtable \
    test_dis test_tokenize 2>&1 | tee "$project_root/logs/baseline-tests.log"
