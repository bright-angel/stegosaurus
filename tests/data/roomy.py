"""Carrier with a high dead-zone density.

Most instructions in modern CPython carry an argument, so a plain script has
very little room.  Loops (``GET_ITER``/``END_FOR``), expression statements
(``POP_TOP``) and generator bodies (``YIELD_VALUE``) are the arg-less opcodes
that make room, so this file is built almost entirely out of them.
"""


def tally(values):
    total = 0
    for value in values:
        total += value
    return total


def classify(values):
    buckets = {"even": 0, "odd": 0}
    for value in values:
        if value % 2:
            buckets["odd"] += 1
        else:
            buckets["even"] += 1
    return buckets


def stream(values):
    for value in values:
        yield value
        yield value


def main():
    values = [1, 2, 3, 4, 5]
    tally(values)
    classify(values)
    list(stream(values))
    print("done")


if __name__ == "__main__":
    main()
