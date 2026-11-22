#!/usr/bin/env python3
"""Проганяє серію задач через HTTP API master-вузла PARCS і збирає результати.

Для кожного розміру задачі: послідовна версія та паралельна з різною кількістю
воркерів, кожна конфігурація по --runs разів. Кількість активних воркерів
задається через API enable/disable (так само, як кнопки у вебінтерфейсі).

    python3 scripts/run_experiments.py --master http://IP:8080 --workers 1,2,4 --runs 3
    python3 scripts/run_experiments.py --summary-only        # лише перерахувати таблиці

Сирі відповіді: results/raw/<job>.json; усі прогони: results/runs.csv;
середні значення, прискорення й ефективність: results/summary.csv.
"""
import argparse
import csv
import json
import os
import re
import statistics
import time
import urllib.error
import urllib.request
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOLUTION = os.path.join(ROOT, "solution", "primes_solution.py")
RUN_FIELDS = ["size", "start", "end", "mode", "workers", "run", "job_id",
              "elapsed_seconds", "count", "worker_seconds"]


def http(method, url, body=None, headers=None, timeout=60):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def multipart(fields, files):
    boundary = uuid.uuid4().hex
    out = []
    for name, value in fields.items():
        out.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                    % (boundary, name, value)).encode())
    for name, (filename, data) in files.items():
        out.append(('--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\n'
                    'Content-Type: application/octet-stream\r\n\r\n'
                    % (boundary, name, filename)).encode() + data + b"\r\n")
    out.append(("--%s--\r\n" % boundary).encode())
    return b"".join(out), {"Content-Type": "multipart/form-data; boundary=%s" % boundary}


class Parcs:
    def __init__(self, master):
        self.master = master.rstrip("/")

    def workers(self):
        status, body = http("GET", self.master + "/api/worker")
        if status != 200:
            raise RuntimeError("GET /api/worker -> %d" % status)
        return sorted(json.loads(body)["workers"], key=lambda w: w["id"])

    def use_workers(self, n):
        workers = self.workers()
        if len(workers) < n:
            raise RuntimeError("need %d workers, master sees %d" % (n, len(workers)))
        for i, w in enumerate(workers):
            state = "enable" if i < n else "disable"
            status, _ = http("POST", "%s/api/worker/%d/%s" % (self.master, w["id"], state))
            if status != 200:
                raise RuntimeError("cannot %s worker %d" % (state, w["id"]))

    def job_ids(self):
        _, body = http("GET", self.master + "/jobs")
        return [int(x) for x in re.findall(r'class="well job-well[^"]*" data-job-id="(\d+)"', body.decode())]

    def submit(self, name, solution, input_bytes):
        before = set(self.job_ids())
        body, headers = multipart({"job_name": name},
                                  {"solution_file": ("solution.py", solution),
                                   "input_file": ("input.txt", input_bytes)})
        status, _ = http("POST", self.master + "/api/job", body, headers)
        if status != 200:
            raise RuntimeError("submit %s -> %d" % (name, status))
        new = set(self.job_ids()) - before
        if len(new) != 1:
            raise RuntimeError("cannot identify job id for %s: %s" % (name, sorted(new)))
        return new.pop()

    def wait_output(self, job_id, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            status, body = http("GET", "%s/api/job/%d/output" % (self.master, job_id))
            if status == 200:
                return json.loads(body)
            if status != 400:  # 400 = ще виконується; інше = задача впала
                raise RuntimeError("job %d failed (HTTP %d), see %s/jobs" % (job_id, status, self.master))
            time.sleep(1)
        raise RuntimeError("job %d timed out" % job_id)


def size_label(start, end):
    return "[%d, %d)" % (start, end)


def run(args):
    parcs = Parcs(args.master)
    with open(SOLUTION, "rb") as f:
        solution = f.read()
    results = args.results
    os.makedirs(os.path.join(results, "raw"), exist_ok=True)
    runs_path = os.path.join(results, "runs.csv")
    new_file = not os.path.exists(runs_path)
    print("Workers registered on master: %d" % len(parcs.workers()))

    plan = []
    for end in args.ends:
        plan.append((end, "sequential", 0))
        plan.extend((end, "parallel", n) for n in args.workers)

    with open(runs_path, "a", newline="") as fcsv:
        writer = csv.DictWriter(fcsv, fieldnames=RUN_FIELDS)
        if new_file:
            writer.writeheader()
        for end, mode, n in plan:
            parcs.use_workers(max(n, 1))  # послідовній задачі PARCS теж потрібен >= 1 воркер
            cfg = {"start": args.start, "end": end, "mode": mode}
            for r in range(1, args.runs + 1):
                name = "%s_W%d_E%s_RUN%d" % ("SEQ" if mode == "sequential" else "PAR", n,
                                            "{:.0e}".format(end).replace("+", ""), r)
                job_id = parcs.submit(name, solution, json.dumps(cfg).encode())
                out = parcs.wait_output(job_id, args.timeout)
                with open(os.path.join(results, "raw", "%s.json" % name), "w") as f:
                    json.dump(dict(out, job_id=job_id, job_name=name), f, indent=2)
                writer.writerow({
                    "size": size_label(args.start, end), "start": args.start, "end": end,
                    "mode": mode, "workers": n, "run": r, "job_id": job_id,
                    "elapsed_seconds": out["elapsed_seconds"], "count": out["count"],
                    "worker_seconds": ";".join(str(p["seconds"]) for p in out["parts"]),
                })
                fcsv.flush()
                print("%-24s job #%-3d %8.3f s  primes=%d" % (name, job_id, out["elapsed_seconds"], out["count"]))
    summarize(results)


def summarize(results):
    with open(os.path.join(results, "runs.csv")) as f:
        rows = list(csv.DictReader(f))
    groups = {}
    for r in rows:
        key = (int(r["start"]), int(r["end"]), r["mode"], int(r["workers"]))
        groups.setdefault(key, []).append(r)

    summary = []
    for (start, end) in sorted({k[:2] for k in groups}):
        seq = groups.get((start, end, "sequential", 0))
        seq_mean = statistics.mean(float(r["elapsed_seconds"]) for r in seq) if seq else None
        for key in sorted(k for k in groups if k[:2] == (start, end)):
            rs = groups[key]
            times = [float(r["elapsed_seconds"]) for r in rs]
            counts = {r["count"] for r in rs}
            mean = statistics.mean(times)
            n = key[3]
            speedup = seq_mean / mean if seq_mean else None
            worker_max = [max(float(x) for x in r["worker_seconds"].split(";")) for r in rs if r["worker_seconds"]]
            summary.append({
                "size": size_label(start, end), "mode": key[2], "workers": n, "runs": len(rs),
                "times": ";".join("%.3f" % t for t in times),
                "mean_seconds": "%.3f" % mean,
                "stdev_seconds": "%.3f" % (statistics.stdev(times) if len(times) > 1 else 0.0),
                "speedup": "%.2f" % speedup if speedup else "",
                "efficiency_percent": "%.1f" % (100 * speedup / n) if speedup and n else "",
                "slowest_worker_seconds": "%.3f" % statistics.mean(worker_max) if worker_max else "",
                "count": counts.pop() if len(counts) == 1 else "MISMATCH " + ",".join(sorted(counts)),
            })
    path = os.path.join(results, "summary.csv")
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary[0]))
        writer.writeheader()
        writer.writerows(summary)
    for s in summary:
        print("%-22s %-10s W=%d  mean=%8s  S=%5s  E=%5s  primes=%s" % (
            s["size"], s["mode"], s["workers"], s["mean_seconds"], s["speedup"] or "-",
            s["efficiency_percent"] or "-", s["count"]))
    print("-> %s" % path)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--master", help="URL master-вузла, напр. http://1.2.3.4:8080")
    p.add_argument("--start", type=int, default=0)
    p.add_argument("--ends", default="10000000000", help="кінці діапазонів через кому (розміри задачі)")
    p.add_argument("--workers", default="1,2,4", help="кількості воркерів через кому")
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--timeout", type=int, default=1800, help="ліміт на одну задачу, с")
    p.add_argument("--results", default=os.path.join(ROOT, "results"), help="каталог для результатів")
    p.add_argument("--summary-only", action="store_true")
    args = p.parse_args()
    if args.summary_only:
        summarize(args.results)
        return
    if not args.master:
        p.error("--master is required")
    args.ends = [int(float(x)) for x in args.ends.split(",")]
    args.workers = [int(x) for x in args.workers.split(",")]
    run(args)


if __name__ == "__main__":
    main()
