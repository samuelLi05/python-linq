"""Inspect query IR, disassemble source, or execute it with this interpreter."""

import argparse
import ast
import dis
import json
from pathlib import Path
import sys
import types

from _linq import _inspect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("ir", "dis", "run"):
        command = commands.add_parser(name)
        command.add_argument("--optimize", type=int, choices=(-1, 0, 1, 2), default=-1)
        command.add_argument("file", type=Path)
        if name == "run":
            command.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    path = args.file.resolve()
    source = path.read_bytes()
    if args.command == "ir":
        tree = compile(source, str(path), "exec", flags=ast.PyCF_ONLY_AST,
                       dont_inherit=True, optimize=args.optimize)
        print(json.dumps(_inspect(tree, str(path), optimize=args.optimize), indent=2))
        return
    code = compile(source, str(path), "exec", dont_inherit=True, optimize=args.optimize)
    if args.command == "dis":
        dis.dis(code)
        return
    module = types.ModuleType("__main__")
    module.__file__ = str(path)
    module.__package__ = None
    module.__spec__ = None
    module.__cached__ = None
    previous_main, previous_argv, previous_path = sys.modules["__main__"], sys.argv, sys.path[:]
    try:
        sys.modules["__main__"] = module
        sys.argv = [str(path), *args.args]
        sys.path[0] = str(path.parent)
        exec(code, module.__dict__)
    finally:
        sys.modules["__main__"] = previous_main
        sys.argv = previous_argv
        sys.path[:] = previous_path


if __name__ == "__main__":
    main()
