"""Native filtering/projection, captured parameters, and deferred callbacks."""

events = []


def keep(score, minimum):
    result = score >= minimum
    events.append((score, result))
    return result


def make(scores, minimum, scale):
    return (from score in scores where keep(score, minimum) select score * scale)


if __name__ == "__main__":
    query = make([50, 80, 90, 80], 80, 2)
    print("before:", events)
    iterator = iter(query)
    print("first:", next(iterator))
    print("events:", events)
    print("remaining:", list(iterator))
