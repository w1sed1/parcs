# -*- coding: utf-8 -*-
"""Пошук простих чисел у діапазоні [start, end) для PARCS-Python.

Алгоритм: сегментоване решето Ератосфена, у сегменті зберігаються лише непарні
числа (bytearray, 1 байт на число). Викреслення кратних виконується
присвоєнням зрізу bytearray, тобто на рівні C, тому код швидкий навіть на
чистому Python 2.7, який є в образі hummer12007/parcs-node.

Режими (поле "mode" у вхідному JSON):
  sequential - master рахує весь діапазон сам, воркери не задіяні;
  parallel   - діапазон ділиться на рівні частини за кількістю воркерів,
               кожен воркер просіює свою частину й повертає підсумок.

Файл працює і як розв'язок PARCS (клас Solver), і як звичайний скрипт:
  python primes_solution.py input.json [output.json]   # послідовний режим
"""
from __future__ import division, print_function

import json
import math
import sys
import time

try:
    from Pyro4 import expose
except ImportError:  # запуск без PARCS (локальні тести, CLI)
    def expose(func):
        return func

try:
    range = xrange  # Python 2: лінивий діапазон
except NameError:
    pass

DEFAULT_SEGMENT = 1 << 22  # чисел у сегменті: 2 МБ bytearray (лише непарні), вміщується в кеш
EDGE_PRIMES = 10           # скільки перших/останніх простих повертати


def isqrt(n):
    """Ціла частина квадратного кореня (math.isqrt немає в Python 2)."""
    if n < 0:
        raise ValueError("isqrt of negative number")
    x = int(math.sqrt(n))
    while x * x > n:
        x -= 1
    while (x + 1) * (x + 1) <= n:
        x += 1
    return x


def base_primes(limit):
    """Непарні прості p <= limit (класичне решето, лише непарні)."""
    if limit < 3:
        return []
    size = (limit - 1) // 2          # індекс i відповідає числу 2*i + 3
    sieve = bytearray(b"\x01") * size
    for i in range((isqrt(limit) - 1) // 2):
        if sieve[i]:
            p = 2 * i + 3
            first = (p * p - 3) // 2
            sieve[first::p] = bytearray((size - 1 - first) // p + 1)
    return [2 * i + 3 for i, flag in enumerate(sieve) if flag]


def _edge_primes(seg, first_odd, k):
    """Перші та останні k простих у сегменті (пошук find/rfind на рівні C)."""
    head, pos = [], seg.find(b"\x01")
    while pos != -1 and len(head) < k:
        head.append(first_odd + 2 * pos)
        pos = seg.find(b"\x01", pos + 1)
    tail, pos = [], seg.rfind(b"\x01")
    while pos != -1 and len(tail) < k:
        tail.append(first_odd + 2 * pos)
        pos = seg.rfind(b"\x01", 0, pos)
    tail.reverse()
    return head, tail


def count_primes(start, end, segment=DEFAULT_SEGMENT, k=EDGE_PRIMES):
    """Підсумок по простих у [start, end): кількість, перші й останні k простих.

    Це спільне обчислювальне ядро: його викликає і послідовна версія (на весь
    діапазон), і кожен воркер (на свою частину).
    """
    t0 = time.time()
    start, end = max(int(start), 0), int(end)
    count, head, tail = 0, [], []
    if start <= 2 < end:             # 2 - єдине парне просте
        count, head, tail = 1, [2], [2]
    primes = base_primes(isqrt(end - 1)) if end > 2 else []

    lo = start
    while lo < end:
        hi = min(lo + segment, end)
        first_odd = lo | 1
        size = (hi - first_odd + 1) // 2 if hi > first_odd else 0
        if size > 0:
            seg = bytearray(b"\x01") * size  # seg[i] <-> first_odd + 2*i
            if first_odd == 1:
                seg[0] = 0                   # 1 не є простим
            for p in primes:
                sq = p * p
                if sq >= hi:
                    break
                m = (first_odd + p - 1) // p * p   # перше кратне >= first_odd
                if not m & 1:
                    m += p                          # робимо його непарним
                if m < sq:
                    m = sq                          # саме p не викреслюємо
                i = (m - first_odd) // 2
                if i < size:
                    seg[i::p] = bytearray((size - 1 - i) // p + 1)
            c = seg.count(b"\x01")
            if c:
                count += c
                h, t = _edge_primes(seg, first_odd, k)
                if len(head) < k:
                    head = (head + h)[:k]
                tail = (tail + t)[-k:]
        lo = hi

    return {
        "start": start,
        "end": end,
        "count": count,
        "first": head,
        "last": tail,
        "seconds": round(time.time() - t0, 3),
    }


def split_range(start, end, parts):
    """Ділить [start, end) на parts суміжних частин майже рівної довжини."""
    length = end - start
    return [(start + length * i // parts, start + length * (i + 1) // parts)
            for i in range(parts)]


def merge(results, k=EDGE_PRIMES):
    """Об'єднує підсумки суміжних частин, упорядкованих за start."""
    results = sorted(results, key=lambda r: r["start"])
    head, tail = [], []
    for r in results:
        if len(head) < k:
            head = (head + list(r["first"]))[:k]
        tail = (tail + list(r["last"]))[-k:]
    return {
        "start": results[0]["start"],
        "end": results[-1]["end"],
        "count": sum(r["count"] for r in results),
        "first": head,
        "last": tail,
    }


def read_config(path):
    with open(path) as f:
        cfg = json.load(f)
    start, end = int(cfg["start"]), int(cfg["end"])
    if not 0 <= start < end:
        raise ValueError("need 0 <= start < end, got [%d, %d)" % (start, end))
    mode = cfg.get("mode", "parallel")
    if mode not in ("sequential", "parallel"):
        raise ValueError("mode must be 'sequential' or 'parallel'")
    return start, end, mode, int(cfg.get("segment", DEFAULT_SEGMENT))


def run_sequential(start, end, segment):
    t0 = time.time()
    total = count_primes(start, end, segment)
    total.pop("seconds")
    total.update(mode="sequential", workers=0, parts=[],
                 elapsed_seconds=round(time.time() - t0, 3))
    return total


class Solver:
    """Інтерфейс PARCS: master створює Solver(workers, input, output) і
    викликає solve(); на воркерах Solver() реєструється в Pyro4, а master
    викликає його метод mymap через проксі."""

    def __init__(self, workers=None, input_file_name=None, output_file_name=None):
        self.workers = workers
        self.input_file_name = input_file_name
        self.output_file_name = output_file_name

    def solve(self):
        start, end, mode, segment = read_config(self.input_file_name)
        if mode == "sequential":
            result = run_sequential(start, end, segment)
        else:
            result = self.run_parallel(start, end, segment)
        print("Job result: %s" % json.dumps(result))
        with open(self.output_file_name, "w") as f:
            json.dump(result, f, indent=2)
            f.write("\n")

    def run_parallel(self, start, end, segment):
        t0 = time.time()
        chunks = split_range(start, end, len(self.workers))
        # Проксі воркерів асинхронні: спершу запускаємо всі виклики,
        # потім чекаємо відповіді (.value), тож воркери рахують одночасно.
        futures = [w.mymap(str(lo), str(hi), segment)
                   for w, (lo, hi) in zip(self.workers, chunks)]
        parts = [f.value for f in futures]
        total = merge(parts)
        total.update(mode="parallel", workers=len(self.workers),
                     parts=[{"start": p["start"], "end": p["end"],
                             "count": p["count"], "seconds": p["seconds"]}
                            for p in parts],
                     elapsed_seconds=round(time.time() - t0, 3))
        return total

    @staticmethod
    @expose
    def mymap(lo, hi, segment):
        # Межі передаються рядками, як у прикладі prime_solution.py PARCS,
        # щоб великі числа гарантовано пройшли серіалізацію Pyro4.
        return count_primes(int(lo), int(hi), int(segment))


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        sys.exit("usage: python primes_solution.py input.json [output.json]")
    cfg_start, cfg_end, _, cfg_segment = read_config(sys.argv[1])
    res = run_sequential(cfg_start, cfg_end, cfg_segment)
    text = json.dumps(res, indent=2)
    if len(sys.argv) == 3:
        with open(sys.argv[2], "w") as out:
            out.write(text + "\n")
    print(text)
