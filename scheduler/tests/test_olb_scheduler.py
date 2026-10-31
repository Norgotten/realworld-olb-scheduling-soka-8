"""
Unit test dan uji penanganan error untuk olb_scheduler.py.

Tidak butuh Docker: worker diganti "mock worker" (server HTTP kecil di dalam proses test)
yang bisa diatur untuk berperilaku normal atau rusak dengan berbagai cara.

Jalankan dari folder olb-realworld:
    python -m unittest discover -s scheduler/tests -v
"""
import contextlib
import csv
import hashlib
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
SCHED_DIR = os.path.dirname(HERE)
sys.path.insert(0, SCHED_DIR)
import olb_scheduler as olb  # noqa: E402

DATASET = os.path.join(SCHED_DIR, "dataset", "GoCJ_Dataset_1000.csv")
WORKER_DEFS = [("host1-large", "host1", "LARGE", "1.0"), ("host1-medium", "host1", "MEDIUM", "0.5"),
               ("host1-small", "host1", "SMALL", "0.25"), ("host2-large", "host2", "LARGE", "1.0"),
               ("host2-medium", "host2", "MEDIUM", "0.5"), ("host2-small", "host2", "SMALL", "0.25")]
SPEED = {"LARGE": 4e8, "MEDIUM": 2e8, "SMALL": 1e8}   # MI per detik (sangat cepat supaya test singkat)


# ---------------------------------------------------------------- mock worker
class MockWorker:
    """Server HTTP single-threaded seperti worker.py. `behavior` bisa diganti saat test berjalan.

    behavior:
      ok            : normal
      missing_exec  : respons tanpa exec_time
      http500       : balas HTTP 500
      wrong_task_id : task_id respons salah
      bad_exec      : exec_time negatif
      nan_exec      : exec_time bukan angka valid
      not_json      : respons bukan JSON
      close         : tutup koneksi tanpa membalas
      hang          : tidur lama (lebih lama dari timeout)
      fail_after:K  : normal untuk K task pertama, lalu HTTP 500
    """

    def __init__(self, name, host, vtype, cpus, iter_per_mi=20, behavior="ok", info_name=None):
        self.name, self.host, self.type, self.cpus = name, host, vtype, cpus
        self.iter_per_mi, self.behavior, self.info_name = iter_per_mi, behavior, info_name or name
        self.served = 0
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()
        mock = self

        class H(BaseHTTPRequestHandler):
            def _send(self, code, body, ctype="application/json"):
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/info":
                    self._send(200, {"vm": mock.info_name, "host": mock.host, "type": mock.type,
                                     "cpus": mock.cpus, "iter_per_mi": mock.iter_per_mi})
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self):
                size = int(self.headers.get("Content-Length", 0))
                task = json.loads(self.rfile.read(size) or b"{}")
                with mock.lock:
                    mock.active += 1
                    mock.max_active = max(mock.max_active, mock.active)
                    mock.served += 1
                    k = mock.served
                try:
                    b = mock.behavior
                    if b.startswith("fail_after:") and k > int(b.split(":")[1]):
                        b = "http500"
                    if b == "close":
                        self.close_connection = True
                        self.connection.shutdown(socket.SHUT_RDWR)
                        return
                    if b == "hang":
                        time.sleep(3)
                    if b == "http500":
                        self._send(500, {"error": "boom"})
                        return
                    if b == "not_json":
                        self._send(200, b"<html>bukan json</html>", "text/html")
                        return
                    dur = task["length"] / SPEED[mock.type]
                    time.sleep(dur)
                    resp = {"task_id": task["task_id"], "length": task["length"], "vm": mock.name,
                            "host": mock.host, "type": mock.type, "exec_time": dur}
                    if b == "missing_exec":
                        del resp["exec_time"]
                    elif b == "wrong_task_id":
                        resp["task_id"] = task["task_id"] + 1000
                    elif b == "bad_exec":
                        resp["exec_time"] = -1.0
                    elif b == "nan_exec":
                        resp["exec_time"] = "cepat"
                    self._send(200, resp)
                finally:
                    with mock.lock:
                        mock.active -= 1

            def log_message(self, *a):
                pass

        class QuietServer(HTTPServer):
            def handle_error(self, request, client_address):
                pass   # klien (scheduler) sengaja memutus koneksi pada test timeout

        self.server = QuietServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def quiet(fn, *a, **kw):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = fn(*a, **kw)
    return result, buf.getvalue()


class MockCluster(unittest.TestCase):
    """Base class: menyalakan 6 mock worker untuk setiap test."""

    def setUp(self):
        self.mocks = [MockWorker(*d) for d in WORKER_DEFS]
        self.workers = [{"name": m.name, "host": m.host, "url": m.url} for m in self.mocks]
        self.tmp = tempfile.mkdtemp(prefix="olbtest-")
        self.cfg_path = os.path.join(self.tmp, "workers.mock.json")
        self.write_cfg()

    def write_cfg(self, workers=None):
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            json.dump({"host_price_per_hour": {"host1": 0.0428, "host2": 0.0428},
                       "workers": workers or self.workers}, f)

    def tearDown(self):
        for m in self.mocks:
            m.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def mock(self, name):
        return next(m for m in self.mocks if m.name == name)

    def run_main(self, *extra):
        argv = ["--workers", self.cfg_path, "--out", os.path.join(self.tmp, "hasil"),
                "--dataset", DATASET, *extra]
        return quiet(olb.main, argv)

    def exp_dir(self, name):
        return os.path.join(self.tmp, "hasil", "sequential", name)


# ---------------------------------------------------------------- dataset
class TestDataset(unittest.TestCase):
    def test_sequential_takes_first_n_rows_in_order(self):
        with open(DATASET, encoding="utf-8") as f:
            first_col = [int(line.split(",")[0]) for line in f if line.strip()]
        for n in (1, 100, 500, 1000):
            tasks = olb.load_gocj(DATASET, n)
            self.assertEqual(len(tasks), n)
            self.assertEqual([t["length"] for t in tasks], first_col[:n])
            self.assertEqual([t["task_id"] for t in tasks], list(range(n)))
            self.assertEqual([t["dataset_row"] for t in tasks], list(range(1, n + 1)))

    def test_same_input_every_call(self):
        self.assertEqual(olb.load_gocj(DATASET, 300), olb.load_gocj(DATASET, 300))

    def test_n_1000_contains_all_tasks(self):
        tasks = olb.load_gocj(DATASET, 1000)
        self.assertEqual(len({t["dataset_row"] for t in tasks}), 1000)

    def test_too_many_tasks_rejected(self):
        with self.assertRaises(SystemExit):
            olb.load_gocj(DATASET, 1001)

    def test_header_line_is_skipped(self):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8")
        tmp.write("length,a,b\n83000,1,1\n95000,2,2\n")
        tmp.close()
        try:
            tasks = olb.load_gocj(tmp.name, 2)
            self.assertEqual([t["length"] for t in tasks], [83000, 95000])
            self.assertEqual([t["dataset_row"] for t in tasks], [2, 3])
        finally:
            os.unlink(tmp.name)

    def test_dataset_info(self):
        info = olb.dataset_info(DATASET)
        self.assertEqual(info["rows"], 1000)
        with open(DATASET, "rb") as f:
            self.assertEqual(info["sha256"], hashlib.sha256(f.read()).hexdigest())


# ---------------------------------------------------------------- metrik (rumus lama)
class TestMetrics(unittest.TestCase):
    def test_formulas_unchanged(self):
        workers = [{"name": "a", "host": "h1"}, {"name": "b", "host": "h2"}]
        records = [{"vm": "a", "exec_time": 2.0}, {"vm": "b", "exec_time": 1.5}, {"vm": "b", "exec_time": 2.5}]
        m = olb.compute_metrics(records, 5.0, workers, {"h1": 0.0428, "h2": 0.0428})
        self.assertAlmostEqual(m["makespan"], 5.0)
        self.assertAlmostEqual(m["cost"], (0.0428 + 0.0428) * 5.0 / 3600)
        self.assertAlmostEqual(m["ru"], (2.0 + 4.0) / (2 * 5.0) * 100)          # 60%
        self.assertAlmostEqual(m["di"], (4.0 - 2.0) / 3.0)                       # 0.6667
        self.assertEqual(m["count"], {"a": 1, "b": 2})

    def test_build_plan_orders(self):
        self.assertEqual(olb.build_plan([100, 200], 2, "run", None),
                         [(100, 1), (200, 1), (100, 2), (200, 2)])
        self.assertEqual(olb.build_plan([100, 200], 2, "size", None),
                         [(100, 1), (100, 2), (200, 1), (200, 2)])
        self.assertEqual(olb.build_plan([100, 200], 3, "run", [2]), [(100, 2), (200, 2)])


# ---------------------------------------------------------------- validasi
class TestValidation(unittest.TestCase):
    def setUp(self):
        self.workers = [{"name": "w1", "host": "h1"}, {"name": "w2", "host": "h1"}]
        self.tasks = [{"task_id": i, "length": 1000 * (i + 1), "dataset_row": i + 1} for i in range(4)]
        self.records = [
            {"task_id": 0, "length_mi": 1000, "vm": "w1", "host": "h1", "start": 0.0, "finish": 1.0, "exec_time": 0.9, "dataset_row": 1, "status": "ok"},
            {"task_id": 1, "length_mi": 2000, "vm": "w2", "host": "h1", "start": 0.0, "finish": 2.0, "exec_time": 1.9, "dataset_row": 2, "status": "ok"},
            {"task_id": 2, "length_mi": 3000, "vm": "w1", "host": "h1", "start": 1.0, "finish": 4.0, "exec_time": 2.9, "dataset_row": 3, "status": "ok"},
            {"task_id": 3, "length_mi": 4000, "vm": "w2", "host": "h1", "start": 2.0, "finish": 6.0, "exec_time": 3.9, "dataset_row": 4, "status": "ok"},
        ]

    def check(self, records, makespan=6.0):
        return olb.validate_run(self.tasks, records, makespan, self.workers)

    def test_valid_run_passes(self):
        self.assertEqual(self.check(self.records), [])

    def test_missing_task(self):
        self.assertTrue(any("hilang" in p for p in self.check(self.records[:3], 4.0)))

    def test_duplicate_task(self):
        recs = self.records + [dict(self.records[0], start=6.0, finish=7.0)]
        self.assertTrue(any("duplikat" in p for p in self.check(recs, 7.0)))

    def test_wrong_length(self):
        recs = [dict(self.records[0], length_mi=999)] + self.records[1:]
        self.assertTrue(any("length" in p for p in self.check(recs)))

    def test_unknown_worker(self):
        recs = [dict(self.records[0], vm="w9")] + self.records[1:]
        self.assertTrue(any("tidak dikenal" in p for p in self.check(recs)))

    def test_overlap_on_one_worker(self):
        recs = list(self.records)
        recs[2] = dict(recs[2], start=0.5)     # w1 mulai task 2 sebelum task 0 selesai
        self.assertTrue(any("sebelum task" in p for p in self.check(recs)))

    def test_exec_longer_than_wait(self):
        recs = [dict(self.records[0], exec_time=5.0)] + self.records[1:]
        self.assertTrue(any("lebih besar" in p for p in self.check(recs)))

    def test_invalid_makespan(self):
        self.assertTrue(any("makespan" in p for p in self.check(self.records, -1.0)))
        self.assertTrue(any("makespan" in p for p in self.check(self.records, 5.0)))

    def test_metric_validation(self):
        m = olb.compute_metrics(self.records, 6.0, self.workers, {"h1": 1})
        self.assertEqual(olb.validate_metrics(m, self.records, self.workers), [])
        m["ru"] = 150.0
        self.assertTrue(olb.validate_metrics(m, self.records, self.workers))

    def test_parse_task_response(self):
        task = {"task_id": 3, "length": 1000}
        ok = {"task_id": 3, "length": 1000, "vm": "w1", "exec_time": 0.5}
        self.assertEqual(olb.parse_task_response(ok, task, "w1"), 0.5)
        bad = [
            {k: v for k, v in ok.items() if k != "exec_time"},
            dict(ok, task_id=4), dict(ok, length=1), dict(ok, vm="w2"),
            dict(ok, exec_time=-1), dict(ok, exec_time=float("nan")), dict(ok, exec_time="1"),
            dict(ok, exec_time=True), [1, 2], None,
        ]
        for b in bad:
            with self.assertRaises(ValueError, msg=repr(b)):
                olb.parse_task_response(b, task, "w1")


# ---------------------------------------------------------------- OLB dengan mock worker
class TestOlbWithMockWorkers(MockCluster):
    def test_all_tasks_once_and_valid(self):
        tasks = olb.load_gocj(DATASET, 100)
        records, makespan = olb.run_olb(tasks, self.workers, iter_per_mi=20)
        self.assertEqual(olb.validate_run(tasks, records, makespan, self.workers), [])
        self.assertEqual(sorted(r["task_id"] for r in records), list(range(100)))
        self.assertEqual(sum(m.served for m in self.mocks), 100)       # dikirim tepat sekali
        for m in self.mocks:
            self.assertGreater(m.served, 0, f"{m.name} tidak dapat task")
            self.assertEqual(m.max_active, 1, f"{m.name} mengerjakan >1 task bersamaan")
        met = olb.compute_metrics(records, makespan, self.workers, {"host1": 0.0428, "host2": 0.0428})
        self.assertEqual(olb.validate_metrics(met, records, self.workers), [])

    def test_initial_tie_break_follows_list_order(self):
        tasks = olb.load_gocj(DATASET, 6)
        records, _ = olb.run_olb(tasks, self.workers)
        by_id = {r["task_id"]: r["vm"] for r in records}
        self.assertEqual([by_id[i] for i in range(6)], [w["name"] for w in self.workers])

    def test_scheduler_waits_when_all_workers_busy(self):
        one = self.workers[:1]
        tasks = olb.load_gocj(DATASET, 8)
        records, makespan = olb.run_olb(tasks, one)
        self.assertEqual(len(records), 8)
        self.assertEqual(olb.validate_run(tasks, records, makespan, one), [])
        self.assertEqual(self.mocks[0].max_active, 1)

    def test_idle_worker_with_earliest_ready_time_is_chosen(self):
        # 2 worker, task pertama besar ke w1 dan kecil ke w2; w2 selesai duluan -> task 3 ke w2
        two = self.workers[:2]
        tasks = [{"task_id": 0, "length": 400_000_000 // 10, "dataset_row": 1},   # ~0.1 s di LARGE
                 {"task_id": 1, "length": 1_000, "dataset_row": 2},
                 {"task_id": 2, "length": 1_000, "dataset_row": 3}]
        records, _ = olb.run_olb(tasks, two)
        by_id = {r["task_id"]: r["vm"] for r in records}
        self.assertEqual(by_id[0], "host1-large")
        self.assertEqual(by_id[1], "host1-medium")
        self.assertEqual(by_id[2], "host1-medium")

    def _expect_failure(self, behavior, timeout=5.0):
        self.mock("host2-small").behavior = behavior
        tasks = olb.load_gocj(DATASET, 60)
        t = time.perf_counter()
        with self.assertRaises(olb.RunFailed) as ctx:
            olb.run_olb(tasks, self.workers, task_timeout=timeout)
        elapsed = time.perf_counter() - t
        err = ctx.exception
        self.assertTrue(any("host2-small" in e for e in err.errors), err.errors)
        self.assertFalse(any(r["vm"] == "host2-small" for r in err.records),
                         "task gagal tidak boleh tercatat sebagai berhasil")
        self.assertLess(len(err.records), 60)
        return elapsed, err

    def test_failure_missing_exec_time(self):
        self._expect_failure("missing_exec")

    def test_failure_http_500(self):
        self._expect_failure("http500")

    def test_failure_wrong_task_id(self):
        self._expect_failure("wrong_task_id")

    def test_failure_negative_exec_time(self):
        self._expect_failure("bad_exec")

    def test_failure_non_numeric_exec_time(self):
        self._expect_failure("nan_exec")

    def test_failure_not_json(self):
        self._expect_failure("not_json")

    def test_failure_connection_closed(self):
        self._expect_failure("close")

    def test_failure_timeout_does_not_hang(self):
        elapsed, _ = self._expect_failure("hang", timeout=0.5)
        self.assertLess(elapsed, 4.0)

    def test_failure_connection_refused(self):
        dead = dict(self.workers[5], url=f"http://127.0.0.1:{free_port()}")
        workers = self.workers[:5] + [dead]
        with self.assertRaises(olb.RunFailed) as ctx:
            olb.run_olb(olb.load_gocj(DATASET, 30), workers)
        self.assertTrue(any("host2-small" in e for e in ctx.exception.errors))

    def test_all_workers_fail_does_not_hang(self):
        for m in self.mocks:
            m.behavior = "http500"
        t = time.perf_counter()
        with self.assertRaises(olb.RunFailed):
            olb.run_olb(olb.load_gocj(DATASET, 50), self.workers)
        self.assertLess(time.perf_counter() - t, 3.0)

    def test_check_workers(self):
        infos, problems = olb.check_workers(self.workers, 20, verbose=False)
        self.assertEqual(problems, [])
        self.assertEqual(len(infos), 6)

    def test_check_workers_detects_wrong_iter(self):
        self.mock("host1-medium").iter_per_mi = 10
        _, problems = olb.check_workers(self.workers, 20, verbose=False)
        self.assertTrue(any("ITER_PER_MI=10" in p for p in problems))

    def test_check_workers_detects_wrong_identity(self):
        self.mock("host2-large").info_name = "host1-large"     # salah port / salah konfigurasi
        _, problems = olb.check_workers(self.workers, 20, verbose=False)
        self.assertTrue(any("mengaku" in p for p in problems))

    def test_check_workers_detects_unreachable(self):
        workers = self.workers[:5] + [dict(self.workers[5], url=f"http://127.0.0.1:{free_port()}")]
        _, problems = olb.check_workers(workers, 20, verbose=False)
        self.assertTrue(any("tidak bisa dihubungi" in p for p in problems))


# ---------------------------------------------------------------- program utuh: simpan, resume, no-overwrite
def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class TestMainProgram(MockCluster):
    SIZES = ["--sizes", "20", "40", "60", "--runs", "3"]

    def read(self, name, file):
        with open(os.path.join(self.exp_dir(name), file), encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))

    def test_check_mode(self):
        code, out = self.run_main("--check")
        self.assertEqual(code, 0)
        self.assertEqual(out.count("  OK   "), 6)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "hasil")))

    def test_check_mode_rejects_wrong_iter(self):
        self.mock("host2-medium").iter_per_mi = 40
        code, out = self.run_main("--check")
        self.assertEqual(code, 2)
        self.assertIn("TIDAK SIAP", out)

    def test_full_experiment_files_and_report(self):
        code, _ = self.run_main("--name", "e1", *self.SIZES)
        self.assertEqual(code, 0)
        d = self.exp_dir("e1")
        rows = self.read("e1", "ringkasan.csv")
        self.assertEqual(len(rows), 9)
        self.assertEqual({(int(r["tasks"]), int(r["run"])) for r in rows},
                         {(n, r) for n in (20, 40, 60) for r in (1, 2, 3)})
        # urutan default 'run': run 1 semua ukuran dulu
        self.assertEqual([(r["tasks"], r["run"]) for r in rows[:3]], [("20", "1"), ("40", "1"), ("60", "1")])
        self.assertEqual([r["urutan"] for r in rows], [str(i) for i in range(1, 10)])
        for r in rows:
            self.assertEqual(r["sampling"], "sequential")
            self.assertEqual(r["iter_per_mi"], "20")
            self.assertEqual(r["status"], "ok")
            tasks = self.read("e1", os.path.join("tasks", f"tasks_{r['tasks']}_run{r['run']}.csv"))
            self.assertEqual(len(tasks), int(r["tasks"]))
            self.assertEqual(sum(int(r[f"task_{w['name']}"]) for w in self.workers), int(r["tasks"]))
        cfg = json.load(open(os.path.join(d, "konfigurasi.json"), encoding="utf-8"))
        self.assertEqual(cfg["iter_per_mi"], 20)
        self.assertEqual(cfg["sampling"], "sequential")
        self.assertEqual(len(self.read("e1", "kinerja_worker.csv")), 9 * 6)
        report = open(os.path.join(d, "HASIL_REALWORLD.md"), encoding="utf-8").read()
        self.assertIn("9 dari 9 run selesai", report)
        self.assertIn("Estimated Compute Cost (USD)", report)
        self.assertIn("±", report)
        code, out = quiet(olb.main, ["--out", os.path.join(self.tmp, "hasil"), "--name", "e1",
                                     "--dataset", DATASET, "--verifikasi"])
        self.assertEqual(code, 0, out)
        self.assertIn("VERIFIKASI OK: 9 run", out)

    def test_existing_experiment_is_never_overwritten(self):
        self.run_main("--name", "e2", "--sizes", "20", "--runs", "1")
        d = self.exp_dir("e2")
        before = {f: sha(os.path.join(root, f)) for root, _, fs in os.walk(d) for f in fs}
        code, out = self.run_main("--name", "e2", "--sizes", "20", "--runs", "1")
        self.assertEqual(code, 2)
        self.assertIn("--resume", out)
        after = {f: sha(os.path.join(root, f)) for root, _, fs in os.walk(d) for f in fs}
        self.assertEqual(before, after)

    def test_historical_files_in_out_root_untouched(self):
        root = os.path.join(self.tmp, "hasil")
        os.makedirs(root)
        old = {"ringkasan.csv": "tasks,run\n100,1\n", "HASIL_REALWORLD.md": "# lama\n",
               "tasks_100_run1.csv": "task_id\n0\n"}
        for f, txt in old.items():
            with open(os.path.join(root, f), "w", encoding="utf-8") as fh:
                fh.write(txt)
        self.run_main("--name", "e3", "--sizes", "20", "--runs", "1")
        for f, txt in old.items():
            self.assertEqual(open(os.path.join(root, f), encoding="utf-8").read(), txt)

    def test_sessions_by_run_id_and_resume(self):
        code, _ = self.run_main("--name", "e4", *self.SIZES, "--run-ids", "1", "--note", "credits 86")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.read("e4", "ringkasan.csv")), 3)
        code, _ = self.run_main("--name", "e4", "--resume", "--run-ids", "2")
        self.assertEqual(code, 0)
        code, _ = self.run_main("--name", "e4", "--resume")
        self.assertEqual(code, 0)
        rows = self.read("e4", "ringkasan.csv")
        self.assertEqual(len(rows), 9)
        self.assertEqual([r["sesi"] for r in rows], ["1"] * 3 + ["2"] * 3 + ["3"] * 3)
        sessions = self.read("e4", "log_sesi.csv")
        self.assertEqual([s["status"] for s in sessions], ["selesai"] * 3)
        self.assertEqual(sessions[0]["catatan"], "credits 86")
        # resume lagi saat semua selesai: tidak ada run baru, data tetap 9
        code, out = self.run_main("--name", "e4", "--resume")
        self.assertEqual(code, 0)
        self.assertIn("0 akan dijalankan", out)
        self.assertEqual(len(self.read("e4", "ringkasan.csv")), 9)

    def test_resume_rejects_changed_plan_or_iter(self):
        self.run_main("--name", "e5", "--sizes", "20", "--runs", "2", "--run-ids", "1")
        code, out = self.run_main("--name", "e5", "--resume", "--sizes", "20", "40")
        self.assertEqual(code, 2)
        self.assertIn("konfigurasi berbeda", out)
        for m in self.mocks:
            m.iter_per_mi = 30
        code, out = self.run_main("--name", "e5", "--resume", "--expect-iter", "30")
        self.assertEqual(code, 2)
        self.assertIn("ITER_PER_MI", out)

    def test_resume_without_experiment(self):
        code, out = self.run_main("--name", "tidak-ada", "--resume")
        self.assertEqual(code, 2)

    def test_failure_mid_experiment_keeps_previous_runs_then_resume(self):
        # host2-small rusak setelah 30 task: run 20 dan 40 berhasil, lalu run berikutnya gagal
        self.mock("host2-small").behavior = "fail_after:12"
        code, out = self.run_main("--name", "e6", *self.SIZES, "--order", "size")
        self.assertEqual(code, 2)
        self.assertIn("RUN GAGAL", out)
        rows = self.read("e6", "ringkasan.csv")
        self.assertGreaterEqual(len(rows), 1)
        self.assertLess(len(rows), 9)
        done_before = [(r["tasks"], r["run"]) for r in rows]
        log = open(os.path.join(self.exp_dir("e6"), "gagal", "log_kegagalan.txt"), encoding="utf-8").read()
        self.assertIn("GAGAL", log)
        self.assertIn("host2-small", log)
        self.assertEqual(self.read("e6", "log_sesi.csv")[0]["status"][:8], "berhenti")
        # run yang gagal tidak punya file task di tasks/
        task_files = os.listdir(os.path.join(self.exp_dir("e6"), "tasks"))
        self.assertEqual(len(task_files), len(rows))
        # perbaiki worker, lanjutkan
        self.mock("host2-small").behavior = "ok"
        code, _ = self.run_main("--name", "e6", "--resume")
        self.assertEqual(code, 0)
        rows = self.read("e6", "ringkasan.csv")
        self.assertEqual(len(rows), 9)
        self.assertEqual([(r["tasks"], r["run"]) for r in rows[:len(done_before)]], done_before)
        code, out = quiet(olb.main, ["--out", os.path.join(self.tmp, "hasil"), "--name", "e6",
                                     "--dataset", DATASET, "--verifikasi"])
        self.assertEqual(code, 0, out)

    def test_worker_down_before_run_stops_session(self):
        self.run_main("--name", "e7", "--sizes", "20", "40", "--runs", "1")
        # tambah eksperimen baru, matikan satu worker setelah cek awal lewat konfigurasi rusak
        workers = self.workers[:5] + [dict(self.workers[5], url=f"http://127.0.0.1:{free_port()}")]
        self.write_cfg(workers)
        code, out = self.run_main("--name", "e8", "--sizes", "20", "--runs", "1")
        self.assertEqual(code, 2)
        self.assertIn("TIDAK SIAP", out)
        self.assertFalse(os.path.exists(self.exp_dir("e8")))

    def test_orphan_task_file_is_moved_not_overwritten(self):
        self.run_main("--name", "e9", "--sizes", "20", "--runs", "2", "--run-ids", "1")
        orphan = os.path.join(self.exp_dir("e9"), "tasks", "tasks_20_run2.csv")
        with open(orphan, "w", encoding="utf-8") as f:
            f.write("task_id\nsisa-crash\n")
        code, _ = self.run_main("--name", "e9", "--resume")
        self.assertEqual(code, 0)
        moved = [f for f in os.listdir(os.path.join(self.exp_dir("e9"), "gagal")) if "tidak_lengkap" in f]
        self.assertEqual(len(moved), 1)
        self.assertEqual(len(self.read("e9", os.path.join("tasks", "tasks_20_run2.csv"))), 20)

    def test_verifikasi_detects_tampered_summary(self):
        self.run_main("--name", "e10", "--sizes", "20", "--runs", "1")
        path = os.path.join(self.exp_dir("e10"), "ringkasan.csv")
        rows = self.read("e10", "ringkasan.csv")
        rows[0]["makespan"] = str(float(rows[0]["makespan"]) + 1)
        with open(path, "w", encoding="utf-8", newline="") as f:
            wr = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            wr.writeheader()
            wr.writerows(rows)
        code, out = quiet(olb.main, ["--out", os.path.join(self.tmp, "hasil"), "--name", "e10",
                                     "--dataset", DATASET, "--verifikasi"])
        self.assertEqual(code, 1)
        self.assertIn("makespan", out)

    def test_status_command(self):
        self.run_main("--name", "e11", "--sizes", "20", "40", "--runs", "2", "--run-ids", "1")
        code, out = quiet(olb.main, ["--out", os.path.join(self.tmp, "hasil"), "--name", "e11", "--status"])
        self.assertEqual(code, 0)
        self.assertIn("2 dari 4 run selesai", out)


if __name__ == "__main__":
    unittest.main()
