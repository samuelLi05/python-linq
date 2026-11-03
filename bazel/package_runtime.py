"""Package the declared foreign-CC install tree as a relocatable distribution."""

import argparse
import gzip
import io
from pathlib import Path
import tarfile


def normalize(info):
    info.uid = info.gid = info.mtime = 0
    info.uname = info.gname = ""
    info.pax_headers = {}
    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # The tree-artifact root itself may be a sandbox symlink; archive its contents.
    runtime = args.runtime.resolve()
    if not (runtime / "bin/python3.14d").is_file():
        raise ValueError("The install artifact does not contain bin/python3.14d")
    if not (runtime / "lib/python3.14/encodings").is_dir():
        raise ValueError("The install artifact does not contain the standard library")
    # Inputs are immutable: write the launcher directly into the archive.
    script = b'''#!/usr/bin/env bash
set -euo pipefail
runtime="$(cd -- "$(dirname -- "$0")/.." && pwd)"
export PYTHONHOME="$runtime"
export PYTHONNOUSERSITE=1
exec "$runtime/bin/python3.14d" "$@"
'''
    with args.output.open("wb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as compressed:
            # Bazel stages tree-artifact files as symlinks in the sandbox.
            # Store their bytes so the distribution is independent of its cache.
            with tarfile.open(fileobj=compressed, mode="w", dereference=True) as archive:
                archive.add(runtime, arcname="python-linq", filter=normalize)
                info = tarfile.TarInfo("python-linq/bin/python-linq")
                info.mode, info.size = 0o755, len(script)
                archive.addfile(normalize(info), io.BytesIO(script))


if __name__ == "__main__":
    main()
