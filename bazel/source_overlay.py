"""Materialize a deterministic source archive from declared upstream and overrides."""

import argparse
import copy
import json
from pathlib import Path, PurePosixPath
import posixpath
import tarfile


def safe_relative(name):
    path = PurePosixPath(name)
    if not path.parts or path.is_absolute() or ".." in path.parts or ".git" in path.parts:
        raise ValueError(f"Unsafe source path: {name}")
    return path.as_posix()


def normalized(info):
    info.uid = info.gid = info.mtime = 0
    info.uname = info.gname = ""
    info.pax_headers = {}
    return info


def add_override(archive, relative, path):
    path = Path(path)
    # Bazel may stage a declared source input as a sandbox symlink.
    if not path.is_file():
        raise ValueError(f"Override must be a regular file: {relative}")
    info = tarfile.TarInfo("source/" + relative)
    info.size = path.stat().st_size
    info.mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
    with path.open("rb") as contents:
        archive.addfile(normalized(info), contents)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--overrides", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    overrides = {safe_relative(name): path for name, path in json.loads(args.overrides.read_text()).items()}
    for relative in overrides:
        if any(parent.as_posix() in overrides for parent in PurePosixPath(relative).parents):
            raise ValueError(f"Conflicting override paths: {relative}")

    with tarfile.open(args.archive, "r:*") as upstream, tarfile.open(args.output, "w") as output:
        root = tarfile.TarInfo("source")
        root.type, root.mode = tarfile.DIRTYPE, 0o755
        output.addfile(normalized(root))
        remaining = dict(overrides)
        members = upstream.getmembers()
        for member in members:
            parts = PurePosixPath(member.name).parts
            if not parts or parts[0] != "Python-3.14.0":
                raise ValueError(f"Unexpected archive prefix: {member.name}")
            if len(parts) == 1:
                continue
            relative = safe_relative(PurePosixPath(*parts[1:]).as_posix())
            if relative in overrides:
                if not member.isfile():
                    raise ValueError(f"Override collides with directory or link: {relative}")
            if member.isfile() and any(name.startswith(relative + "/") for name in overrides):
                raise ValueError(f"Override traverses an upstream file: {relative}")
            if member.issym():
                safe_relative(posixpath.normpath(posixpath.join(str(PurePosixPath(relative).parent), member.linkname)))
                if any(name.startswith(relative + "/") for name in overrides):
                    raise ValueError(f"Override traverses upstream symlink: {relative}")
        for member in members:
            parts = PurePosixPath(member.name).parts
            if len(parts) == 1:
                continue
            relative = PurePosixPath(*parts[1:]).as_posix()
            if relative in remaining:
                add_override(output, relative, remaining.pop(relative))
                continue
            info = copy.copy(member)
            info.name = "source/" + relative
            if info.islnk():
                link = PurePosixPath(info.linkname).parts
                if not link or link[0] != "Python-3.14.0":
                    raise ValueError(f"Unexpected hardlink target: {info.linkname}")
                info.linkname = "source/" + safe_relative(PurePosixPath(*link[1:]).as_posix())
            if not (info.isfile() or info.isdir() or info.issym() or info.islnk()):
                raise ValueError(f"Unsupported archive entry: {relative}")
            contents = upstream.extractfile(member) if member.isfile() else None
            output.addfile(normalized(info), contents)
            if contents is not None:
                contents.close()
        for relative, path in sorted(remaining.items()):
            add_override(output, relative, path)


if __name__ == "__main__":
    main()
