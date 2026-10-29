#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
missing_tools=0

printf 'Workspace: %s\n' "$project_root"
for tool in git gcc make python3; do
    if command -v "$tool" >/dev/null 2>&1; then
        printf '%s: %s\n' "$tool" "$(command -v "$tool")"
    else
        printf 'Missing required tool: %s\n' "$tool" >&2
        missing_tools=1
    fi
done

if command -v dpkg-query >/dev/null 2>&1; then
    packages=(build-essential pkg-config libssl-dev zlib1g-dev libbz2-dev
        libreadline-dev libsqlite3-dev libffi-dev liblzma-dev libncurses-dev
        libgdbm-dev libgdbm-compat-dev uuid-dev tk-dev libzstd-dev)
    missing_packages=()
    for package in "${packages[@]}"; do
        package_status="$(dpkg-query -W -f='${Status}' "$package" 2>/dev/null || true)"
        if [[ "$package_status" != 'install ok installed' ]]; then
            missing_packages+=("$package")
        fi
    done
    if (( ${#missing_packages[@]} )); then
        printf '\nMissing recommended Ubuntu development packages:\n'
        printf '  %s\n' "${missing_packages[@]}"
        printf '\nInstall from a normal terminal if desired:\nsudo apt-get update\nsudo apt-get install'
        printf ' %s' "${missing_packages[@]}"
        printf '\n'
    else
        printf '\nAll listed Ubuntu development packages are installed.\n'
    fi
fi

if [[ -f "$project_root/cpython/configure" ]]; then
    printf '\nCPython source: present\n'
else
    printf '\nCPython source: not present\n'
fi
if [[ -x "$project_root/build/debug/python" ]]; then
    "$project_root/build/debug/python" -VV
else
    printf 'Debug interpreter: not built\n'
fi

exit "$missing_tools"
