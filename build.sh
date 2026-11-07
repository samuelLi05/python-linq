#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$project_root"
bazel_args=(build //:dist)
if [[ -n "${BUILD_JOBS:-}" ]]; then
    if [[ ! "$BUILD_JOBS" =~ ^[1-9][0-9]*$ ]]; then
        printf 'BUILD_JOBS must be a positive integer.\n' >&2
        exit 1
    fi
    bazel_args+=("--action_env=CPYTHON_BUILD_JOBS=$BUILD_JOBS")
fi
"${BAZEL:-bazel}" "${bazel_args[@]}" "$@"

mkdir -p "$project_root/build"
stage="$(mktemp -d "$project_root/build/.python-linq.XXXXXX")"
trap 'rm -rf "$stage"' EXIT
cp "$project_root/bazel-bin/python-linq.tar.gz" "$stage/python-linq.tar.gz"
tar -xzf "$stage/python-linq.tar.gz" -C "$stage"
[[ -x "$stage/python-linq/bin/python3.14d" ]]
[[ -d "$stage/python-linq/lib/python3.14/encodings" ]]
chmod -R u+w "$stage/python-linq"

if [[ -e "$project_root/build/python-linq" ]]; then
    mv "$project_root/build/python-linq" "$stage/previous"
fi
mv "$stage/python-linq" "$project_root/build/python-linq"
mv "$stage/python-linq.tar.gz" "$project_root/build/python-linq.tar.gz"
cat > "$stage/python" <<'LAUNCHER'
#!/usr/bin/env bash
set -euo pipefail
build_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
exec "$build_dir/python-linq/bin/python-linq" "$@"
LAUNCHER
chmod +x "$stage/python"
mv "$stage/python" "$project_root/build/python"
printf 'Interpreter: %s/build/python\nArchive: %s/build/python-linq.tar.gz\n' \
    "$project_root" "$project_root"
