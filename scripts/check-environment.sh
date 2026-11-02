#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$project_root/scripts/common.sh"
missing_tools=0

printf 'Workspace: %s\n' "$project_root"
printf 'Platform: %s\n' "$(uname -s)"
compiler="${CC:-cc}"
for tool in git "${compiler%% *}" make python3; do
    if command -v "$tool" >/dev/null 2>&1; then
        printf '%s: %s\n' "$tool" "$(command -v "$tool")"
    else
        printf 'Missing required tool: %s\n' "$tool" >&2
        missing_tools=1
    fi
done

if [[ "$(uname -s)" == Darwin ]]; then
    if ! xcode-select -p >/dev/null 2>&1 || ! xcrun --find clang >/dev/null 2>&1; then
        printf '\nApple build tools are missing. Run: xcode-select --install\n' >&2
        missing_tools=1
    fi
    if command -v brew >/dev/null 2>&1; then
        brew_prefix="$(brew --prefix)"
        missing_packages=()
        for package in pkgconf openssl@3 readline sqlite xz zstd; do
            if [[ ! -d "$brew_prefix/opt/$package" ]]; then
                missing_packages+=("$package")
            fi
        done
        if (( ${#missing_packages[@]} )); then
            printf '\nMissing recommended Homebrew dependencies. Install with:\nbrew install'
            printf ' %s' "${missing_packages[@]}"
            printf '\n'
        else
            printf '\nAll listed Homebrew dependencies are installed.\n'
        fi
    else
        printf '\nHomebrew is optional; it supplies libraries for additional standard-library modules.\n'
        printf 'See docs/SETUP.md for macOS dependency instructions.\n'
    fi
elif [[ "$(uname -s)" == Linux ]] && command -v dpkg-query >/dev/null 2>&1; then
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
        printf '\nMissing recommended Debian/Ubuntu development packages:\n'
        printf '  %s\n' "${missing_packages[@]}"
        printf '\nInstall from a normal terminal if desired:\nsudo apt-get update\nsudo apt-get install'
        printf ' %s' "${missing_packages[@]}"
        printf '\n'
    else
        printf '\nAll listed Debian/Ubuntu development packages are installed.\n'
    fi
elif [[ "$(uname -s)" == Linux ]]; then
    printf '\nInstall development libraries with your distribution package manager.\n'
    printf 'See docs/SETUP.md for the required tools and recommended libraries.\n'
fi

if [[ -f "$project_root/cpython/configure" ]]; then
    printf '\nCPython source: present\n'
else
    printf '\nCPython source: not present\n'
fi
if interpreter="$(find_debug_interpreter "$project_root/build/debug")"; then
    "$interpreter" -VV
else
    printf 'Debug interpreter: not built\n'
fi

exit "$missing_tools"
