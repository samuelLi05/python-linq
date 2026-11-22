"""Inspect query IR, disassemble source, or execute it with this interpreter."""

import argparse
import ast
import dis
import json
from pathlib import Path
import sys
import types

from _linq import _inspect


def ir_summary(report):
    for query in report["queries"]:
        source = query["operators"][0]
        source_name = source.get("binding", {}).get("name", source["expression"])
        if "input_plan" in source:
            source_name = f"q{source['input_plan']}"
        filters = sum(op["kind"] == "Filter" for op in query["operators"])
        identity = query["operators"][-1]["identity"]
        producer = f" producer=q{query['producer_plan']}" if "producer_plan" in query else ""
        print(f"q{query['id']} {query['owner']['name']}:{query['location'][0]} "
              f"mode={query['mode']} source={source_name} filters={filters} "
              f"identity={identity}{producer}")
    for rewrite in report["optimization"]["rewrites"]:
        before, after = rewrite["before"], rewrite["after"]
        print(f"rewrite q{after['id']}: {before['mode']} -> {after['mode']} "
              f"producer=q{after['producer_plan']}")


def analysis_summary(report):
    for function in report["analysis"]["functions"]:
        if function["status"] == "unknown":
            reasons = ", ".join(f"{reason['code']}@{reason['location'][0]}"
                                for reason in function["reasons"])
            print(f"function {function['name']}: unknown ({reasons})")
    queries = {query["id"]: query for query in report["queries"]}
    for candidate in report["analysis"]["candidates"]:
        query = queries[candidate["consumer"]]
        producer = candidate["producer_plan"]
        producer_text = f" producer=q{producer}" if producer is not None else ""
        reaching = candidate.get("reaching_definitions")
        reaching_text = f" reaching={reaching}" if reaching is not None else ""
        print(f"q{candidate['consumer']} {query['owner']['name']}:{query['location'][0]} "
              f"merge={candidate['status']} reason={candidate['reason']}"
              f"{producer_text}{reaching_text}")


def dis_summary(code):
    found = False

    def visit(unit):
        nonlocal found
        factories = [op.argval for op in dis.get_instructions(unit)
                     if op.opname == "IMPORT_FROM" and op.argval in ("_query", "_fused_query")]
        if factories:
            print(f"{unit.co_qualname}:{unit.co_firstlineno} factories={factories}")
            found = True
        if unit.co_name in ("linq_where", "linq_select"):
            parameters = unit.co_varnames[:unit.co_argcount + unit.co_kwonlyargcount]
            print(f"{unit.co_qualname}:{unit.co_firstlineno} "
                  f"parameters={parameters} freevars={unit.co_freevars}")
            found = True
        for constant in unit.co_consts:
            if isinstance(constant, types.CodeType):
                visit(constant)

    visit(code)
    if not found:
        print("No query factory imports or clause code objects.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("ir", "analyze", "dis", "run"):
        command = commands.add_parser(name)
        command.add_argument("--optimize", type=int, choices=(-1, 0, 1, 2), default=-1)
        command.add_argument("--fusion", action=argparse.BooleanOptionalAction, default=False)
        command.add_argument("file", type=Path)
        if name == "run":
            command.add_argument("args", nargs=argparse.REMAINDER)
        else:
            command.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    path = args.file.resolve()
    source = path.read_bytes()
    flags = ast.PyCF_LINQ_FUSION if args.fusion else 0
    if args.command in ("ir", "analyze"):
        tree = compile(source, str(path), "exec", flags=ast.PyCF_ONLY_AST | flags,
                       dont_inherit=True, optimize=args.optimize)
        report = _inspect(tree, str(path), optimize=args.optimize, fusion=args.fusion)
        if args.summary:
            (analysis_summary if args.command == "analyze" else ir_summary)(report)
        else:
            print(json.dumps(report["analysis"] if args.command == "analyze" else report, indent=2))
        return
    code = compile(source, str(path), "exec", flags=flags, dont_inherit=True, optimize=args.optimize)
    if args.command == "dis":
        (dis_summary if args.summary else dis.dis)(code)
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
