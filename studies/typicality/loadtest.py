"""Load test: N simulated Prolific participants rate every item at the same time.

    python loadtest.py [--n 300] [--think 1.0] [--ramp 10] [--study study.yaml] [--keep]

Runs on a throwaway copy of this study folder (own study_id, port and data folder), so the real
data, Potato state and running servers are never touched. Each participant: Prolific entry URL
-> consent (agree) -> the jsPsych page -> /typ/start -> one /typ/rate per item, with a
think time of think * U(0.5, 1.5) seconds before each answer. Reports latency per request type,
errors, and whether every session's data is complete on disk.
"""

import argparse
import html
import http.cookiejar
import json
import random
import re
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
SKIP = shutil.ignore_patterns("responses_data", "annotation_output", "layouts", "project.sqlite*",
                              "admin_api_key.txt", "__pycache__", "*.log", "export--*")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Participant:
    def __init__(self, base, i, think, log):
        self.base, self.i, self.think, self.log = base, i, think, log
        self.rng = random.Random(i)
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.pid = f"{i:024x}"

    def call(self, kind, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, headers={"Content-Type": "application/json"})
        t0 = time.perf_counter()
        try:
            with self.op.open(req, timeout=60) as f:
                status, raw = f.status, f.read()
        except urllib.error.HTTPError as e:
            status, raw = e.code, e.read()
        except Exception as e:          # connection refused/reset, timeout
            status, raw = repr(e), b""
        self.log(kind, (time.perf_counter() - t0) * 1000, status)
        return status, raw

    def run(self):
        ids = f"PROLIFIC_PID={self.pid}&STUDY_ID={'5' * 24}&SESSION_ID={self.i + 10**6:024x}"
        _, page = self.call("entry", f"/?{ids}")
        # the consent answer: a checkbox (its input is named consent_agree:::<label>) or the radio "I agree"
        m = re.search(r'name="(consent_agree:::[^"]+)"', page.decode())
        key = html.unescape(m[1]) if m else "consent_agree:::I agree"
        self.call("consent", "/updateinstance", {"instance_id": "__phase_page__", "annotations": {key: "true"}})
        self.call("consent", "/annotate", {"action": "next_instance", "instance_id": "__phase_page__"})
        self.call("page", "/")
        status, raw = self.call("start", "/typ/start")
        st = json.loads(raw) if status == 200 else {"done": True}
        if st["done"]:
            return
        n_values = round((st["max_value"] - st["min_value"]) / st["step"])
        for i in range(st["i_trial"], len(st["items"])):
            time.sleep(self.think * self.rng.uniform(0.5, 1.5))
            value = st["min_value"] + st["step"] * self.rng.randint(0, n_values)
            status, _ = self.call("rate", "/typ/rate", {"i_trial": i, "value": value, "rt_ms": 1000, "first_touch_ms": 300})
            if status != 200:
                return


def check_data(folder: Path, n: int):
    """Problems with the stored sessions (empty list = every session complete and consistent)."""
    problems = []
    metas = sorted(folder.glob("*/*/session.json"))
    if len(metas) != n:
        problems.append(f"{len(metas)} session folders, expected {n}")
    for p in metas:
        m = json.loads(p.read_text())
        lines = [json.loads(x) for x in (p.parent / "trials.jsonl").read_text().splitlines() if x.strip()]
        if m["status"] != "complete":
            problems.append(f"{m['sid']}: status {m['status']}")
        if [r["i_trial"] for r in lines] != list(range(m["n_trials"])):
            problems.append(f"{m['sid']}: trials.jsonl has i_trial {[r['i_trial'] for r in lines][:5]}..., expected 0..{m['n_trials'] - 1}")
        if not m.get("prolific"):
            problems.append(f"{m['sid']}: Prolific ids missing")
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=300, help="participants")
    ap.add_argument("--think", type=float, default=1.0, help="mean seconds between answers")
    ap.add_argument("--ramp", type=float, default=10.0, help="participants arrive evenly over this many seconds")
    ap.add_argument("--study", default="study.yaml")
    ap.add_argument("--keep", action="store_true", help="keep the throwaway copy (path is printed)")
    a = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="typ-loadtest-"))
    work = tmp / "study"
    shutil.copytree(ROOT, work, ignore=SKIP)
    study = yaml.safe_load((ROOT / a.study).read_text())
    study.update(study_id=study["study_id"] + "--loadtest", port=free_port(),
                 assets_root=str((ROOT / study["assets_root"]).resolve()))
    (work / a.study).write_text(yaml.safe_dump(study, sort_keys=False))
    base = f"http://127.0.0.1:{study['port']}"

    server_log = open(tmp / "server.log", "w")
    server = subprocess.Popen([sys.executable, "run.py", a.study], cwd=work, stdout=server_log, stderr=subprocess.STDOUT)
    try:
        for _ in range(120):
            try:
                urllib.request.urlopen(base + "/", timeout=1)
                break
            except urllib.error.HTTPError:
                break
            except Exception:
                time.sleep(0.5)
        else:
            sys.exit(f"server did not start, see {tmp / 'server.log'}")

        lat, errors, lock = defaultdict(list), defaultdict(int), threading.Lock()

        def log(kind, ms, status):
            with lock:
                lat[kind].append(ms)
                if status != 200:
                    errors[f"{kind} {status}"] += 1

        print(f"{a.n} participants, think {a.think} s, ramp {a.ramp} s, server {base}, copy {work}")
        t0 = time.time()
        threads = []
        for i in range(a.n):
            th = threading.Thread(target=Participant(base, i, a.think, log).run)
            time.sleep(a.ramp / a.n)
            th.start()
            threads.append(th)
        for th in threads:
            th.join()
        wall = time.time() - t0

        print(f"\nwall time {wall:.0f} s, {sum(map(len, lat.values()))} requests, "
              f"{sum(map(len, lat.values())) / wall:.0f} req/s")
        print(f"{'request':10} {'n':>7} {'p50 ms':>8} {'p99 ms':>8} {'max ms':>8}")
        for kind, v in lat.items():
            q = statistics.quantiles(v, n=100, method="inclusive") if len(v) > 1 else v * 99
            print(f"{kind:10} {len(v):7d} {q[49]:8.1f} {q[98]:8.1f} {max(v):8.1f}")
        print("errors:", dict(errors) or "none")
        problems = check_data(work / study["output_root"] / study["study_id"], a.n)
        print("data:", "complete (every session complete, trials 0..n-1 in order, Prolific ids stored)"
              if not problems else f"{len(problems)} problems, e.g. {problems[:5]}")
    finally:
        server.terminate()
        server.wait(timeout=30)
        server_log.close()
        if a.keep:
            print("kept:", tmp)
        else:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    main()
