# -*- coding: utf-8 -*-
"""Перевірка коректності ядра. Працює і в Python 3, і в Python 2.7 (образ PARCS):
    python tests/test_primes.py
"""
from __future__ import print_function

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "solution"))
import primes_solution as ps  # noqa: E402


def naive_primes(start, end):
    def is_prime(n):
        if n < 2:
            return False
        d = 2
        while d * d <= n:
            if n % d == 0:
                return False
            d += 1
        return True
    return [n for n in range(start, end) if is_prime(n)]


def expected(start, end, k=ps.EDGE_PRIMES):
    p = naive_primes(start, end)
    return len(p), p[:k], p[-k:] if p else []


class FakeFuture(object):
    def __init__(self, value):
        self.value = value


class FakeWorker(object):
    """Імітує асинхронний Pyro4-проксі воркера."""
    def mymap(self, lo, hi, segment):
        return FakeFuture(ps.Solver.mymap(lo, hi, segment))


class PrimesTest(unittest.TestCase):
    def check(self, start, end, segment):
        r = ps.count_primes(start, end, segment)
        c, head, tail = expected(start, end)
        self.assertEqual((r["count"], r["first"], r["last"]), (c, head, tail),
                         "range [%d, %d) segment %d" % (start, end, segment))

    def test_small_edges(self):
        for start, end in [(0, 1), (0, 2), (0, 3), (2, 3), (1, 2), (3, 4),
                           (0, 10), (1, 10), (4, 5), (24, 29), (0, 100)]:
            for seg in (1, 2, 3, 7, 64):
                self.check(start, end, seg)

    def test_random_ranges_and_segments(self):
        rnd = random.Random(2026)
        for _ in range(300):
            start = rnd.randrange(0, 200000)
            end = start + rnd.randrange(1, 3000)
            self.check(start, end, rnd.choice([1, 2, 5, 17, 100, 1024, 1 << 20]))

    def test_known_prime_counts(self):
        # pi(10^6) = 78 498, pi(10^7) = 664 579
        self.assertEqual(ps.count_primes(0, 10 ** 6)["count"], 78498)
        self.assertEqual(ps.count_primes(0, 10 ** 7, segment=1 << 16)["count"], 664579)

    def test_large_numbers_around_1e10(self):
        start = 10 ** 10
        self.check(start, start + 2000, 333)

    def test_split_covers_range_exactly(self):
        for start, end, parts in [(0, 10, 3), (5, 6, 4), (10 ** 10, 10 ** 10 + 7, 4), (0, 100, 1)]:
            chunks = ps.split_range(start, end, parts)
            self.assertEqual(len(chunks), parts)
            self.assertEqual(chunks[0][0], start)
            self.assertEqual(chunks[-1][1], end)
            for (a, b), (c, _) in zip(chunks, chunks[1:]):
                self.assertEqual(b, c)

    def test_parallel_equals_sequential(self):
        start, end = 123456, 1234567
        seq = ps.run_sequential(start, end, 1 << 14)
        for n in (1, 2, 3, 4, 7):
            par = ps.Solver([FakeWorker() for _ in range(n)]).run_parallel(start, end, 1 << 14)
            for key in ("start", "end", "count", "first", "last"):
                self.assertEqual(par[key], seq[key], "%s with %d workers" % (key, n))
            self.assertEqual(len(par["parts"]), n)


if __name__ == "__main__":
    unittest.main(verbosity=1)
