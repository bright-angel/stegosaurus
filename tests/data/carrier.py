"""Carrier script used by the test suite.

Deliberately contains loops, branches, calls and a comprehension so that the
compiled bytecode has a healthy number of instructions without arguments.
"""

from math import sqrt


def fib_recursive(n):
    if n < 2:
        return n
    return fib_recursive(n - 1) + fib_recursive(n - 2)


def fib_closed(n):
    return int(((1 + sqrt(5)) ** n - (1 - sqrt(5)) ** n) / (2**n * sqrt(5)))


def main():
    results = [fib_recursive(12), fib_closed(12)]
    for value in results:
        print(value)
    return results


if __name__ == "__main__":
    main()
