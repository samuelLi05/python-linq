"""Declared source-overlay and distribution actions for the foreign CPython build."""

load("@rules_cc//cc:defs.bzl", "CcInfo")
load("@rules_cc//cc/common:cc_common.bzl", "cc_common")

def _list(value):
    return value.to_list() if type(value) == "depset" else value

def _static_dependency_impl(ctx):
    info = ctx.attr.dep[CcInfo]
    compilation_context = info.compilation_context
    if ctx.attr.public_header_roots:
        headers = []
        includes = []
        for header in compilation_context.headers.to_list():
            for root in ctx.attr.public_header_roots:
                marker = "/" + root + "/"
                if marker in header.path and header.basename != "config.h":
                    headers.append(header)
                    includes.append(header.path.split(marker)[0] + marker[:-1])
                    break
        if not headers:
            fail("No public headers found for " + str(ctx.attr.dep.label))
        # Private headers/defines in a transitive CcInfo are build details of
        # that library. In particular xz's util.h shadows macOS's <util.h>.
        compilation_context = cc_common.create_compilation_context(
            headers = depset(headers),
            includes = depset(includes),
        )
    linker_inputs = []
    archives = {}
    for linker_input in info.linking_context.linker_inputs.to_list():
        libraries = []
        for library in _list(linker_input.libraries):
            original = library.pic_static_library or library.static_library
            if original == None:
                fail("CPython dependencies must supply static archives: " + str(linker_input.owner))
            name = original.basename.replace(".pic.a", ".a")
            name = ctx.attr.library_aliases.get(name, name)
            if name in archives:
                previous, archive = archives[name]
                if previous.path != original.path:
                    fail("Conflicting native dependency archive: " + name)
            else:
                archive = ctx.actions.declare_file(ctx.label.name + "/" + name)
                ctx.actions.run_shell(
                    inputs = [original],
                    outputs = [archive],
                    arguments = [original.path, archive.path],
                    command = 'cp "$1" "$2"',
                    mnemonic = "CPythonStaticDependency",
                )
                archives[name] = (original, archive)
            libraries.append(cc_common.create_library_to_link(
                actions = ctx.actions,
                static_library = archive,
                alwayslink = library.alwayslink,
            ))
        linker_inputs.append(cc_common.create_linker_input(
            owner = linker_input.owner,
            libraries = depset(libraries),
            user_link_flags = depset(_list(linker_input.user_link_flags)),
            additional_inputs = depset(_list(linker_input.additional_inputs)),
        ))
    return [CcInfo(
        compilation_context = compilation_context,
        linking_context = cc_common.create_linking_context(linker_inputs = depset(linker_inputs)),
    )]

_static_dependency = rule(
    implementation = _static_dependency_impl,
    attrs = {
        "dep": attr.label(providers = [CcInfo], mandatory = True),
        "library_aliases": attr.string_dict(),
        "public_header_roots": attr.string_list(),
    },
)

def cpython_native_deps():
    """Select PIC archives with conventional names for configure's -l probes."""
    dependencies = {
        "zlib": "@zlib//:z",
        "bzip2": "@bzip2//:bz2",
        "xz": "@xz//:lzma",
        "zstd": "@zstd//:zstd",
        "sqlite": "@sqlite3//:sqlite3",
        "ffi": "@libffi//:libffi",
        "ssl": "@openssl//:ssl",
        "crypto": "@openssl//:crypto",
        "readline": "@readline//:readline",
        "ncurses": "@ncurses//:ncurses",
        "panel": "@ncurses//:panel",
    }
    targets = []
    for name, dependency in dependencies.items():
        target = name + "_static"
        _static_dependency(
            name = target,
            dep = dependency,
            library_aliases = {"liblibffi.a": "libffi.a"} if name == "ffi" else {},
            public_header_roots = [
                "src/liblzma/api",
                "_virtual_includes/lzma",
                "_virtual_includes/lzma_src_liblzma_api",
            ] if name == "xz" else [],
        )
        targets.append(":" + target)
    return targets

def _cpython_source_impl(ctx):
    archive = ctx.actions.declare_file(ctx.label.name + "/source.tar")
    configure = ctx.actions.declare_file(ctx.label.name + "/configure")
    manifest = ctx.actions.declare_file(ctx.label.name + ".overrides.json")
    overrides = {}
    for file in ctx.files.overrides:
        if not file.short_path.startswith("patches/"):
            fail("Overrides must be files below patches/: " + file.short_path)
        relative = file.short_path[len("patches/"):]
        if relative in overrides:
            fail("Duplicate override: " + relative)
        overrides[relative] = file.path
    ctx.actions.write(manifest, json.encode(overrides))
    arguments = ctx.actions.args()
    arguments.add("--archive", ctx.file.archive)
    arguments.add("--overrides", manifest)
    arguments.add("--output", archive)
    ctx.actions.run(
        executable = ctx.executable._overlay,
        arguments = [arguments],
        inputs = depset([ctx.file.archive, manifest] + ctx.files.overrides),
        tools = [ctx.attr._overlay[DefaultInfo].files_to_run],
        outputs = [archive],
        mnemonic = "CPythonSourceOverlay",
        progress_message = "Preparing CPython with declared source overrides",
    )
    # Foreign CC detects the root from the parent of its input artifacts. Keep
    # the archive and configure adapter together; unpack only in its scratch copy.
    ctx.actions.write(
        configure,
        """#!/usr/bin/env bash
set -euo pipefail
: "${PYTHON_FOR_REGEN:?The declared host Python is required}"
adapter_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
"$PYTHON_FOR_REGEN" -c 'import sys, tarfile; tarfile.open(sys.argv[1]).extractall(sys.argv[2], filter="data")' "$adapter_dir/source.tar" "$PWD"
# Make removes the quotes from Bazel's __DATE__/__TIME__ redaction flags.
# CPython accepts explicit DATE/TIME strings; preserve them through Make's shell.
export CPPFLAGS="$CPPFLAGS -DDATE='\\\"redacted\\\"' -DTIME='\\\"redacted\\\"'"
exec "$PWD/source/configure" "$@" --srcdir="$PWD/source"
""",
        is_executable = True,
    )
    return [
        DefaultInfo(files = depset([archive, configure])),
        OutputGroupInfo(archive = depset([archive])),
    ]

cpython_source = rule(
    implementation = _cpython_source_impl,
    attrs = {
        "archive": attr.label(allow_single_file = True, mandatory = True),
        "overrides": attr.label_list(allow_files = True),
        "_overlay": attr.label(
            default = Label("//bazel:source_overlay"),
            executable = True,
            cfg = "exec",
        ),
    },
)

def _cpython_distribution_impl(ctx):
    archive = ctx.actions.declare_file("python-linq.tar.gz")
    arguments = ctx.actions.args()
    arguments.add("--runtime", ctx.file.runtime.path)
    arguments.add("--output", archive)
    ctx.actions.run(
        executable = ctx.executable._packager,
        arguments = [arguments],
        inputs = [ctx.file.runtime],
        tools = [ctx.attr._packager[DefaultInfo].files_to_run],
        outputs = [archive],
        mnemonic = "CPythonDistribution",
        progress_message = "Packaging the Python LINQ runtime",
    )
    return [DefaultInfo(files = depset([archive]))]

cpython_distribution = rule(
    implementation = _cpython_distribution_impl,
    attrs = {
        "runtime": attr.label(allow_single_file = True, mandatory = True),
        "_packager": attr.label(
            default = Label("//bazel:package_runtime"),
            executable = True,
            cfg = "exec",
        ),
    },
)

def _cpython_launcher_impl(ctx):
    executable = ctx.actions.declare_file(ctx.label.name)
    runtime = ctx.file.runtime
    runfile = ctx.workspace_name + "/" + runtime.short_path
    ctx.actions.write(
        executable,
        """#!/usr/bin/env bash
set -euo pipefail
runfiles="$(cd -- "${RUNFILES_DIR:-$0.runfiles}" && pwd)"
runtime="$runfiles/%s"
if [[ ! -x "$runtime/bin/python3.14d" ]]; then
    printf 'Runtime not found in runfiles: %%s\\n' "$runtime" >&2
    exit 1
fi
export PYTHONHOME="$runtime"
export PYTHONNOUSERSITE=1
exec "$runtime/bin/python3.14d" "$@"
""" % runfile,
        is_executable = True,
    )
    return [DefaultInfo(
        executable = executable,
        runfiles = ctx.runfiles(files = [runtime]),
    )]

cpython_launcher = rule(
    implementation = _cpython_launcher_impl,
    executable = True,
    attrs = {"runtime": attr.label(allow_single_file = True, mandatory = True)},
)
