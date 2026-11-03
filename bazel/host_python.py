"""Expose the declared build Python as an interpreter command to Make."""

import os
import sys

# CPython's generators import siblings beside the script. Bazel's Python
# launcher enables safe-path mode, which suppresses that normal script path.
env = dict(os.environ)
env.pop("PYTHONSAFEPATH", None)
# Preserve PYTHONPATH: Make sets it explicitly for `python -m pegen`.
env["PYTHONNOUSERSITE"] = "1"
os.execve(sys.executable, [sys.executable, *sys.argv[1:]], env)
