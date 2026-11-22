"""Valid queries with an ambiguous producer or unsupported control flow."""


def ambiguous(values, flag):
    if flag:
        p = (from x in values where x > 0 select x)
    else:
        p = (from x in values where x < 0 select x)
    q = (from y in p where y % 2 == 0 select y)
    return q


def unsupported(values):
    p = (from x in values where x > 0 select x)
    q = (from y in p where y % 2 == 0 select y)
    for unused in []:
        pass
    return q


if __name__ == "__main__":
    values = [-4, -3, -2, 1, 2, 3, 4]
    print("positive branch:", list(ambiguous(values, True)))
    print("negative branch:", list(ambiguous(values, False)))
    print("unsupported flow:", list(unsupported(values)))
