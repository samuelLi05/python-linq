#!/usr/bin/env bash
# Shared helpers, compatible with the Bash 3.2 shipped by macOS.

find_debug_interpreter() {
    local candidate
    for candidate in "$1/python" "$1/python.exe"; do
        if [[ -x "$candidate" && ! -d "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

configure_build_environment() {
    configure_args=(--with-pydebug)
    [[ "$(uname -s)" == Darwin ]] || return 0
    command -v brew >/dev/null 2>&1 || return 0

    local brew_prefix formula prefix
    brew_prefix="$(brew --prefix)"
    for formula in openssl@3 readline sqlite xz zstd; do
        prefix="$brew_prefix/opt/$formula"
        [[ -d "$prefix" ]] || continue
        export CPPFLAGS="${CPPFLAGS:+$CPPFLAGS }-I$prefix/include"
        export LDFLAGS="${LDFLAGS:+$LDFLAGS }-L$prefix/lib"
        export PKG_CONFIG_PATH="$prefix/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
        if [[ "$formula" == openssl@3 ]]; then
            # Configure needs this explicit prefix when pkg-config is absent.
            configure_args+=("--with-openssl=$prefix")
        fi
    done
}
