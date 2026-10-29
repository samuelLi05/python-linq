#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source_dir="$project_root/cpython"
build_dir="$project_root/build/debug"
log_dir="$project_root/logs"
build_jobs="${BUILD_JOBS:-4}"

if [[ ! "$build_jobs" =~ ^[1-9][0-9]*$ ]]; then
    printf 'BUILD_JOBS must be a positive integer.\n' >&2
    exit 1
fi

bash "$project_root/scripts/check-environment.sh"
mkdir -p "$build_dir" "$log_dir"

if [[ ! -f "$source_dir/configure" ]]; then
    if [[ -e "$source_dir" ]]; then
        printf 'Expected a CPython checkout at %s; refusing to overwrite it.\n' "$source_dir" >&2
        exit 1
    fi
    if ! git clone --depth 1 --branch v3.14.0 --single-branch \
        https://github.com/python/cpython.git "$source_dir" 2>&1 | tee "$log_dir/source.log"; then
        printf 'Source download failed. See docs/SETUP.md for manual source acquisition.\n' >&2
        exit 1
    fi
    git -C "$source_dir" switch -c python-linq
fi

if [[ ! -f "$source_dir/Include/patchlevel.h" ]] ||
    ! grep -Eq '^#define[[:space:]]+PY_VERSION[[:space:]]+"3\.14\.0"' "$source_dir/Include/patchlevel.h"; then
    printf 'This setup expects CPython 3.14.0; check the source version before building.\n' >&2
    exit 1
fi

cd "$build_dir"
# Reconfigure explicitly on each invocation so the requested debug mode is applied.
"$source_dir/configure" --with-pydebug 2>&1 | tee "$log_dir/configure.log"
make -j"$build_jobs" 2>&1 | tee "$log_dir/build.log"
./python -VV
./python -c 'import sysconfig; assert sysconfig.get_config_var("Py_DEBUG") == 1; print("Debug build verified")'
printf '\nRun baseline tests with: bash %s/scripts/test-baseline.sh\n' "$project_root"
