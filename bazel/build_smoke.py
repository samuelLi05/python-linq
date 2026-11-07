"""Verify declared source overrides and the real, built CPython runtime."""

import argparse
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile

from python.runfiles import runfiles


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--distribution", required=True)
    parser.add_argument("--tests", required=True)
    parser.add_argument("--override", action="append", default=[])
    args = parser.parse_args()
    locations = runfiles.Create()

    def locate(name):
        path = locations.Rlocation(name)
        if path is None or not Path(path).exists():
            raise FileNotFoundError(f"Declared artifact not found: {name}")
        return Path(path).resolve()

    with tarfile.open(locate(args.source)) as source:
        for entry in args.override:
            relative, location = entry.split("=", 1)
            member = source.extractfile("source/" + relative)
            if member is None or member.read() != locate(location).read_bytes():
                raise AssertionError(f"Source artifact does not match override: {relative}")
    print(f"Source artifact matches all {len(args.override)} override files.")

    with tempfile.TemporaryDirectory(prefix="python-linq-smoke-") as directory:
        with tarfile.open(locate(args.distribution)) as distribution:
            if any(member.issym() or member.islnk() for member in distribution.getmembers()):
                raise AssertionError("Distribution must contain files, not Bazel sandbox links")
            distribution.extractall(directory, filter="data")
        runtime = Path(directory) / "python-linq"
        env = {name: value for name, value in os.environ.items() if not name.startswith("PYTHON")}
        result = subprocess.run(
            [str(runtime / "bin/python-linq"), "-s", str(locate(args.tests)), "-v"],
            env=env, cwd=directory, text=True, capture_output=True,
        )
    print(result.stdout, end="")
    print(result.stderr, end="")
    if result.returncode:
        raise RuntimeError(f"Built interpreter exited {result.returncode}:\n{result.stderr}")


if __name__ == "__main__":
    main()
