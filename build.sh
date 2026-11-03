#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$project_root"
bazel_args=()
if [[ -n "${BUILD_JOBS:-}" ]]; then
    if [[ ! "$BUILD_JOBS" =~ ^[1-9][0-9]*$ ]]; then
        printf 'BUILD_JOBS must be a positive integer.\n' >&2
        exit 1
    fi
    bazel_args+=("--action_env=CPYTHON_BUILD_JOBS=$BUILD_JOBS")
fi
if (( $# == 0 )) || [[ "$1" == -* ]]; then
    bazel_args+=(//:dist)
fi
exec "${BAZEL:-bazel}" build "${bazel_args[@]}" "$@"
