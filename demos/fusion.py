"""An eligible filter chain; use --counts with the project's debug build."""

import sys


def make(values):
    p = (from x in values where x > 0 select x)
    q = (from y in p where y % 2 == 0 select y)
    return q


if __name__ == "__main__":
    counts = "--counts" in sys.argv[1:]
    if counts:
        from _linq import _test_counters
        _test_counters(enabled=True, reset=True)
    try:
        print(list(make([1, 2, 3, 4])))
    finally:
        if counts:
            stats = _test_counters(enabled=False)
    if counts:
        print("query objects:", stats["normal_queries"] + stats["fused_queries"])
        print("projection stages:", stats["project_iterators"])
        print("projection calls:", stats["project_calls"])
