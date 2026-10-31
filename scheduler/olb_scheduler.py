"""
Scheduler OLB (Opportunistic Load Balancing) versi real world.

Ide OLB: setiap task diberikan ke worker yang sedang menganggur (idle). Kalau ada beberapa
worker idle, dipilih yang paling awal siap (ready time terkecil), lalu urutan daftar worker
sebagai pemecah seri. OLB tidak melihat kecepatan worker maupun ukuran task.

Bedanya dengan CloudSim:
  - Di CloudSim, ready time DIHITUNG dari model (length / MIPS) sebelum simulasi.
  - Di sini, ready time DIAMATI: worker dianggap siap saat responsnya diterima scheduler,
    yaitu saat ia benar-benar selesai mengerjakan task sebelumnya.

Istilah:
  - host   = Azure VM (host1, host2), tempat worker berjalan.
  - worker = Docker container (host1-large, dst.), representasi logis VM CloudSim.

Contoh pakai:
  python3 olb_scheduler.py --workers workers.local.json --check
  python3 olb_scheduler.py --workers workers.local.json --name uji-lokal --sizes 100 --runs 1
  python3 olb_scheduler.py --workers workers.azure.json --name azure-seq \
      --sizes 100 200 300 400 500 600 700 800 900 1000 --runs 3 --run-ids 1
  python3 olb_scheduler.py --workers workers.azure.json --name azure-seq --resume --run-ids 2
  python3 olb_scheduler.py --name azure-seq --status
  python3 olb_scheduler.py --name azure-seq --verifikasi

Hasil tersimpan di  <out>/sequential/<name>/  dan tidak pernah menimpa file yang sudah ada.

Hanya pakai library bawaan Python.
"""
import argparse
import csv
import hashlib
import json
import math
import os
import platform
import socket
import statistics
import sys
import threading
import time
import urllib.request
from datetime import datetime

SAMPLING = "sequential"          # tahap ini hanya mode sekuensial (N baris pertama)
SCHEMA_VERSION = 2               # versi format folder hasil
DEFAULT_EXPECT_ITER = 20         # ITER_PER_MI yang diharapkan di semua worker
SLOWDOWN_NOTE_PCT = 25.0         # ambang catatan perubahan kecepatan worker (bukan bukti throttling)


class RunFailed(Exception):
    """Satu run gagal: ada task yang tidak selesai dengan benar atau validasi tidak lolos."""

    def __init__(self, errors, records=None):
        super().__init__("; ".join(errors))
        self.errors = errors
        self.records = records or []


# ---------------------------------------------------------------- utilitas jaringan
def http_get(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def http_post(url, payload, timeout=600):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------- dataset
def load_gocj(path, n):
    """Ambil n task pertama dari file GoCJ (angka pertama tiap baris = panjang task dalam MI).

    task_id    : nomor urut task mulai 0 (sama seperti versi sebelumnya)
    dataset_row: nomor baris di file CSV asli (mulai 1), supaya asal task bisa dilacak
    """
    tasks = []
    with open(path, encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            first = line.strip().replace(";", ",").split(",")[0].strip()
            if first.replace(".", "", 1).isdigit():
                tasks.append({"task_id": len(tasks), "length": int(float(first)),
                              "dataset_row": line_no})
            if len(tasks) == n:
                break
    if len(tasks) < n:
        raise SystemExit(f"Dataset {path} cuma punya {len(tasks)} task, diminta {n}.")
    return tasks


def dataset_info(path):
    with open(path, "rb") as f:
        data = f.read()
    rows = sum(1 for line in data.decode("utf-8").splitlines() if line.strip())
    return {"file": os.path.basename(path), "sha256": hashlib.sha256(data).hexdigest(), "rows": rows}


# ---------------------------------------------------------------- cek worker
def check_workers(workers, expect_iter, verbose=True):
    """Hubungi /info semua worker. Kembalikan (info per worker, daftar masalah).

    Masalah yang dicek: worker tidak bisa dihubungi, nama worker tidak cocok dengan file
    konfigurasi, host tidak cocok, atau ITER_PER_MI berbeda dari yang diharapkan.
    """
    infos, problems = {}, []
    for w in workers:
        try:
            info = http_get(w["url"] + "/info")
        except Exception as e:  # noqa: BLE001
            problems.append(f"{w['name']} ({w['url']}) tidak bisa dihubungi: {e}")
            if verbose:
                print(f"  GAGAL {w['name']:<14} {w['url']}: {e}")
            continue
        infos[w["name"]] = info
        if verbose:
            print(f"  OK   {w['name']:<14} host={info.get('host')!s:<6} tipe={info.get('type')!s:<7} "
                  f"cpus={info.get('cpus')!s:<5} iter_per_mi={info.get('iter_per_mi')}")
        if info.get("vm") != w["name"]:
            problems.append(f"{w['name']}: worker di {w['url']} mengaku bernama '{info.get('vm')}'")
        if info.get("host") != w["host"]:
            problems.append(f"{w['name']}: host di worker '{info.get('host')}' != host di konfigurasi '{w['host']}'")
        try:
            iter_ok = int(info.get("iter_per_mi")) == int(expect_iter)
        except (TypeError, ValueError):
            iter_ok = False
        if not iter_ok:
            problems.append(f"{w['name']}: ITER_PER_MI={info.get('iter_per_mi')}, diharapkan {expect_iter}")
    return infos, problems


# ---------------------------------------------------------------- respons worker
def parse_task_response(resp, task, vm):
    """Pastikan respons worker lengkap dan sesuai task yang dikirim. Kembalikan exec_time."""
    if not isinstance(resp, dict):
        raise ValueError(f"respons bukan objek JSON: {resp!r}")
    for key in ("task_id", "length", "vm", "exec_time"):
        if key not in resp:
            raise ValueError(f"respons tidak punya kolom '{key}'")
    if resp["task_id"] != task["task_id"]:
        raise ValueError(f"task_id respons {resp['task_id']} != task yang dikirim {task['task_id']}")
    if int(resp["length"]) != int(task["length"]):
        raise ValueError(f"length respons {resp['length']} != length task {task['length']}")
    if resp["vm"] != vm:
        raise ValueError(f"respons datang dari '{resp['vm']}', bukan '{vm}'")
    exec_time = resp["exec_time"]
    if isinstance(exec_time, bool) or not isinstance(exec_time, (int, float)) \
            or not math.isfinite(exec_time) or exec_time < 0:
        raise ValueError(f"exec_time tidak valid: {exec_time!r}")
    return float(exec_time)


# ---------------------------------------------------------------- inti OLB
def run_olb(tasks, workers, task_timeout=600, iter_per_mi=None):
    """Jalankan satu batch task dengan aturan OLB. Kembalikan (records, makespan).

    Kalau ada task yang gagal (koneksi putus, worker error, respons tidak lengkap atau tidak
    cocok), pengiriman task baru dihentikan, task yang sedang berjalan ditunggu selesai,
    lalu RunFailed dilempar. Task yang gagal tidak pernah dicatat sebagai berhasil.
    """
    names = [w["name"] for w in workers]
    url = {w["name"]: w["url"] for w in workers}
    host_of = {w["name"]: w["host"] for w in workers}
    order = {n: i for i, n in enumerate(names)}

    ready_time = {n: 0.0 for n in names}   # kapan worker terakhir kali jadi siap (detik sejak mulai)
    idle = set(names)                      # worker yang sedang menganggur
    cond = threading.Condition()
    records, errors, threads = [], [], []
    t0 = time.perf_counter()

    def dispatch(vm, task):
        sent = time.perf_counter() - t0
        try:
            resp = http_post(url[vm] + "/task", {"task_id": task["task_id"], "length": task["length"]},
                             timeout=task_timeout)
            exec_time = parse_task_response(resp, task, vm)
        except Exception as e:  # noqa: BLE001  (semua jenis kegagalan diperlakukan sama)
            with cond:
                errors.append(f"{vm} gagal mengerjakan task {task['task_id']} "
                              f"(baris {task.get('dataset_row')}): {type(e).__name__}: {e}")
                # worker yang gagal TIDAK dikembalikan ke daftar idle
                cond.notify_all()
            return
        done = time.perf_counter() - t0
        with cond:
            records.append({
                "task_id": task["task_id"], "length_mi": task["length"], "vm": vm,
                "host": resp.get("host", host_of[vm]), "type": resp.get("type"),
                "start": round(sent, 4), "finish": round(done, 4),
                "exec_time": round(exec_time, 4),
                "dataset_row": task.get("dataset_row"),
                "iterations": task["length"] * iter_per_mi if iter_per_mi else "",
                "status": "ok",
            })
            ready_time[vm] = done      # worker ini siap lagi SEKARANG
            idle.add(vm)
            cond.notify_all()

    for task in tasks:
        with cond:
            while not idle and not errors:
                cond.wait()
            if errors:
                break
            # ===== LANGKAH OLB: pilih worker idle dengan ready time paling awal =====
            vm = min(idle, key=lambda n: (ready_time[n], order[n]))
            idle.remove(vm)
        th = threading.Thread(target=dispatch, args=(vm, task), daemon=True)
        th.start()
        threads.append(th)

    for th in threads:
        th.join()
    records.sort(key=lambda r: r["task_id"])
    if errors:
        raise RunFailed(errors, records)

    makespan = max(r["finish"] for r in records)
    return records, makespan


# ---------------------------------------------------------------- validasi
def validate_run(tasks, records, makespan, workers, tol=1e-3):
    """Periksa hasil satu run. Kembalikan daftar masalah (kosong = lolos)."""
    problems = []
    expected = {t["task_id"]: t for t in tasks}
    names = {w["name"]: w for w in workers}

    if len(records) != len(tasks):
        problems.append(f"jumlah task berhasil {len(records)} != diminta {len(tasks)}")
    ids = [r["task_id"] for r in records]
    dup = sorted({i for i in ids if ids.count(i) > 1}) if len(ids) != len(set(ids)) else []
    if dup:
        problems.append(f"task duplikat: {dup[:10]}")
    missing = sorted(set(expected) - set(ids))
    if missing:
        problems.append(f"task hilang: {missing[:10]}{' ...' if len(missing) > 10 else ''}")
    extra = sorted(set(ids) - set(expected))
    if extra:
        problems.append(f"task tidak dikenal: {extra[:10]}")

    for r in records:
        t = expected.get(r["task_id"])
        tag = f"task {r['task_id']}"
        if t is not None:
            if r["length_mi"] != t["length"]:
                problems.append(f"{tag}: length {r['length_mi']} != dataset {t['length']}")
            if t.get("dataset_row") is not None and r.get("dataset_row") != t["dataset_row"]:
                problems.append(f"{tag}: baris dataset {r.get('dataset_row')} != {t['dataset_row']}")
        if r["vm"] not in names:
            problems.append(f"{tag}: worker tujuan '{r['vm']}' tidak dikenal")
        elif r["host"] != names[r["vm"]]["host"]:
            problems.append(f"{tag}: host '{r['host']}' tidak sesuai worker {r['vm']}")
        if r.get("status") != "ok":
            problems.append(f"{tag}: status {r.get('status')}")
        vals = (r["start"], r["finish"], r["exec_time"])
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in vals):
            problems.append(f"{tag}: waktu tidak valid {vals}")
            continue
        if r["start"] < 0 or r["finish"] < r["start"] or r["exec_time"] < 0:
            problems.append(f"{tag}: urutan waktu tidak masuk akal {vals}")
        if r["exec_time"] > (r["finish"] - r["start"]) + tol:
            problems.append(f"{tag}: exec_time {r['exec_time']} lebih besar dari waktu tunggu scheduler "
                            f"{r['finish'] - r['start']:.4f}")

    # satu worker hanya boleh mengerjakan satu task pada satu waktu
    by_vm = {}
    for r in records:
        by_vm.setdefault(r["vm"], []).append(r)
    for vm, rs in by_vm.items():
        rs = sorted(rs, key=lambda r: r["start"])
        for a, b in zip(rs, rs[1:]):
            if b["start"] < a["finish"] - 1e-4:
                problems.append(f"{vm}: task {b['task_id']} dimulai sebelum task {a['task_id']} selesai")

    if not (isinstance(makespan, (int, float)) and math.isfinite(makespan) and makespan > 0):
        problems.append(f"makespan tidak valid: {makespan!r}")
    elif records and abs(makespan - max(r["finish"] for r in records)) > 1e-9:
        problems.append("makespan tidak sama dengan finish task terakhir")
    return problems


def validate_metrics(m, records, workers):
    problems = []
    if sum(m["count"].values()) != len(records):
        problems.append("jumlah task per worker tidak sama dengan jumlah record")
    total_exec = sum(r["exec_time"] for r in records)
    if abs(sum(m["busy"].values()) - total_exec) > 1e-6 * max(1.0, total_exec):
        problems.append("total waktu sibuk tidak sama dengan jumlah exec_time")
    for k in ("makespan", "cost", "ru", "di"):
        if not math.isfinite(m[k]) or m[k] < 0:
            problems.append(f"metrik {k} tidak valid: {m[k]!r}")
    if m["ru"] > 100.0 + 1e-6:
        problems.append(f"RU {m['ru']:.4f}% melebihi 100%")
    return problems


# ---------------------------------------------------------------- metrik (rumus tidak diubah)
def compute_metrics(records, makespan, workers, host_price):
    busy = {w["name"]: 0.0 for w in workers}
    count = {w["name"]: 0 for w in workers}
    for r in records:
        busy[r["vm"]] += r["exec_time"]
        count[r["vm"]] += 1
    total_busy = sum(busy.values())
    avg_busy = total_busy / len(busy)
    ru = total_busy / (len(busy) * makespan) * 100
    di = (max(busy.values()) - min(busy.values())) / avg_busy if avg_busy else 0.0
    # Estimated Compute Cost: tarif per jam kedua host x makespan (bukan tagihan Azure total)
    cost = sum(host_price.values()) * makespan / 3600
    return {
        "makespan": makespan, "cost": cost, "ru": ru, "di": di,
        "busy": busy, "count": count,
    }


def worker_perf(records, workers, infos, iter_per_mi):
    """Data pendukung (bukan metrik evaluasi): detik per 1 juta iterasi tiap worker."""
    rows = []
    for w in workers:
        rs = [r for r in records if r["vm"] == w["name"]]
        total_mi = sum(r["length_mi"] for r in rs)
        busy = sum(r["exec_time"] for r in rs)
        iters = total_mi * iter_per_mi
        info = infos.get(w["name"], {})
        rows.append({
            "worker": w["name"], "host": w["host"], "type": info.get("type", ""),
            "cpus": info.get("cpus", ""), "jumlah_task": len(rs), "total_mi": total_mi,
            "busy_s": round(busy, 4),
            "detik_per_juta_iterasi": round(busy / (iters / 1e6), 6) if iters else "",
        })
    return rows


# ---------------------------------------------------------------- penyimpanan
def atomic_write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def append_csv_row(path, fieldnames, row):
    new = not os.path.exists(path)
    with open(path, "a", encoding="utf-8", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=fieldnames)
        if new:
            wr.writeheader()
        wr.writerow(row)
        f.flush()
        os.fsync(f.fileno())


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_new_csv(path, rows):
    """Tulis CSV baru. Gagal (FileExistsError) kalau file sudah ada, jadi tidak pernah menimpa."""
    fields = list(rows[0].keys()) if rows else ["task_id"]
    with open(path, "x", encoding="utf-8", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=fields)
        wr.writeheader()
        wr.writerows(rows)


TASK_FIELDS = ["task_id", "length_mi", "vm", "host", "type", "start", "finish", "exec_time",
               "dataset_row", "iterations", "status"]
PERF_FIELDS = ["tasks", "run", "worker", "host", "type", "cpus", "jumlah_task", "total_mi",
               "busy_s", "detik_per_juta_iterasi"]
SESSION_FIELDS = ["sesi", "mulai", "selesai", "run_diminta", "run_selesai", "status", "catatan"]


def summary_fields(workers):
    # kolom lama dipertahankan di depan supaya kompatibel dengan ringkasan.csv versi sebelumnya
    return (["tasks", "run", "makespan", "cost", "ru", "di"]
            + [f"task_{w['name']}" for w in workers]
            + ["sampling", "dataset", "iter_per_mi", "total_mi", "urutan", "sesi",
               "waktu_mulai", "waktu_selesai", "status"])


class Experiment:
    """Satu folder eksperimen: <out>/sequential/<name>/"""

    def __init__(self, out_root, name):
        self.dir = os.path.join(out_root, SAMPLING, name)
        self.name = name
        self.config_path = os.path.join(self.dir, "konfigurasi.json")
        self.summary_path = os.path.join(self.dir, "ringkasan.csv")
        self.perf_path = os.path.join(self.dir, "kinerja_worker.csv")
        self.session_path = os.path.join(self.dir, "log_sesi.csv")
        self.report_path = os.path.join(self.dir, "HASIL_REALWORLD.md")
        self.tasks_dir = os.path.join(self.dir, "tasks")
        self.fail_dir = os.path.join(self.dir, "gagal")
        self.fail_log = os.path.join(self.fail_dir, "log_kegagalan.txt")

    def exists(self):
        return os.path.exists(self.config_path)

    def load_config(self):
        with open(self.config_path, encoding="utf-8") as f:
            return json.load(f)

    def create(self, config):
        os.makedirs(self.tasks_dir, exist_ok=True)
        os.makedirs(self.fail_dir, exist_ok=True)
        with open(self.config_path, "x", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

    def done_runs(self):
        return {(int(r["tasks"]), int(r["run"])) for r in read_csv(self.summary_path)}

    def task_file(self, n, run):
        return os.path.join(self.tasks_dir, f"tasks_{n}_run{run}.csv")

    def log_failure(self, n, run, errors, records):
        os.makedirs(self.fail_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        partial = os.path.join(self.fail_dir, f"tasks_{n}_run{run}_gagal_{stamp}.csv")
        if records:
            write_new_csv(partial, records)
        with open(self.fail_log, "a", encoding="utf-8") as f:
            f.write(f"[{now_iso()}] {n} task, run {run} GAGAL (tidak dimasukkan ke ringkasan)\n")
            for e in errors:
                f.write(f"    - {e}\n")
            if records:
                f.write(f"    task yang sempat selesai: {len(records)} -> {os.path.basename(partial)}\n")
        return partial if records else None

    def quarantine_orphan(self, n, run):
        """File task ada tapi run tidak ada di ringkasan (program berhenti di tengah penyimpanan)."""
        path = self.task_file(n, run)
        if os.path.exists(path):
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            dest = os.path.join(self.fail_dir, f"tasks_{n}_run{run}_tidak_lengkap_{stamp}.csv")
            os.makedirs(self.fail_dir, exist_ok=True)
            os.replace(path, dest)
            return dest
        return None


# ---------------------------------------------------------------- laporan
def _mean_sd(values):
    if not values:
        return float("nan"), None
    sd = statistics.stdev(values) if len(values) >= 2 else None
    return statistics.mean(values), sd


def _fmt(mean, sd, digits):
    if sd is None:
        return f"{mean:.{digits}f}"
    return f"{mean:.{digits}f} ± {sd:.{digits}f}"


def build_report(exp):
    cfg = exp.load_config()
    rows = read_csv(exp.summary_path)
    perf = read_csv(exp.perf_path)
    sizes, runs = cfg["sizes"], cfg["runs"]
    worker_names = [w["name"] for w in cfg["workers"]]
    done = {(int(r["tasks"]), int(r["run"])) for r in rows}
    planned = [(n, r) for n in sizes for r in range(1, runs + 1)]
    missing = [p for p in planned if p not in done]
    price = cfg.get("host_price_per_hour", {})

    L = [
        "# Hasil Pengujian OLB Real World (Sekuensial)", "",
        f"- Eksperimen: `{cfg['name']}`",
        f"- Laporan dibuat ulang otomatis: {now_iso()}",
        f"- Mode sampling: **{cfg['sampling']}** (N baris pertama dataset, urutan sama di setiap run)",
        f"- Dataset: {cfg['dataset']['file']} ({cfg['dataset']['rows']} baris, "
        f"sha256 `{cfg['dataset']['sha256'][:16]}...`)",
        f"- ITER_PER_MI: {cfg['iter_per_mi']} (diverifikasi lewat `/info` keenam worker)",
        f"- Infrastruktur: {len(set(w['host'] for w in cfg['workers']))} host "
        f"({'Azure VM' if 'azure' in cfg.get('workers_file', '').lower() else 'host logis di satu mesin, uji lokal'}), "
        f"{len(cfg['workers'])} worker (Docker container), file `{cfg.get('workers_file', '?')}`",
        f"- Scheduler berjalan di: `{cfg.get('scheduler_host', '?')}`",
        f"- Asumsi tarif host (USD/jam): " + ", ".join(f"{h}={p}" for h, p in price.items()),
        f"- Urutan eksekusi: `{cfg['order']}`",
        f"- Progres: **{len(done)} dari {len(planned)} run selesai**",
    ]
    if missing:
        L.append(f"- Belum dijalankan / belum berhasil: " + ", ".join(f"{n}x run{r}" for n, r in missing))
    L += ["", "Hanya run yang lolos validasi otomatis yang masuk ke tabel di bawah. "
          "Run yang gagal dicatat di folder `gagal/`.", ""]

    L += ["## Rata-rata per jumlah task", "",
          "Nilai ditulis sebagai rata-rata ± simpangan baku sampel (jika minimal 2 run).", "",
          "| Jumlah Task | Run | Makespan (s) | Estimated Compute Cost (USD) | Resource Utilization (%) | Degree of Imbalance |",
          "|---:|---:|---:|---:|---:|---:|"]
    for n in sizes:
        rs = [r for r in rows if int(r["tasks"]) == n]
        if not rs:
            continue
        cells = []
        for k, d in (("makespan", 2), ("cost", 6), ("ru", 2), ("di", 4)):
            mean, sd = _mean_sd([float(r[k]) for r in rs])
            cells.append(_fmt(mean, sd, d))
        L.append(f"| {n} | {len(rs)} | " + " | ".join(cells) + " |")

    L += ["", "## Detail tiap run", "",
          "| Jumlah Task | Run | Urutan | Sesi | Makespan (s) | Est. Cost (USD) | RU (%) | DI | Mulai |",
          "|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for r in sorted(rows, key=lambda r: (int(r["tasks"]), int(r["run"]))):
        L.append(f"| {r['tasks']} | {r['run']} | {r['urutan']} | {r['sesi']} | {float(r['makespan']):.2f} | "
                 f"{float(r['cost']):.6f} | {float(r['ru']):.2f} | {float(r['di']):.4f} | {r['waktu_mulai']} |")

    L += ["", "## Jumlah task per worker", "",
          "| Jumlah Task | Run | " + " | ".join(worker_names) + " |",
          "|---:|---:|" + "---:|" * len(worker_names)]
    for r in sorted(rows, key=lambda r: (int(r["tasks"]), int(r["run"]))):
        L.append(f"| {r['tasks']} | {r['run']} | " + " | ".join(r[f"task_{w}"] for w in worker_names) + " |")

    if perf:
        L += ["", "## Kecepatan worker (data pendukung, bukan metrik evaluasi)", "",
              "Detik per 1 juta iterasi `burn_cpu()` = waktu sibuk worker / (total MI x ITER_PER_MI / 10^6). "
              "Perubahan angka ini antar-run dapat menjadi **indikasi** perubahan performa host "
              "(misalnya CPU credits menipis), tetapi bukan bukti throttling. "
              "Bandingkan dengan grafik CPU Credits Remaining di Azure Monitor.", "",
              "| Jumlah Task | Run | " + " | ".join(worker_names) + " |",
              "|---:|---:|" + "---:|" * len(worker_names)]
        by_run = {}
        for p in perf:
            by_run.setdefault((int(p["tasks"]), int(p["run"])), {})[p["worker"]] = p["detik_per_juta_iterasi"]
        for key in sorted(by_run):
            vals = by_run[key]
            L.append(f"| {key[0]} | {key[1]} | " + " | ".join(
                (f"{float(vals[w]):.4f}" if vals.get(w) not in (None, "") else "-") for w in worker_names) + " |")

    L += ["", "## Cara membaca metrik", "",
          "- **Makespan**: detik dari awal penjadwalan batch sampai respons task terakhir diterima scheduler "
          "(termasuk overhead jaringan).",
          "- **Estimated Compute Cost (USD)**: `(tarif host1 + tarif host2) x makespan / 3600`. "
          "Estimasi biaya komputasi dua host selama makespan, bukan tagihan Azure sebenarnya "
          "(tagihan juga mencakup waktu VM menyala di luar pengujian, disk, IP publik, dll.).",
          "- **Resource Utilization (%)**: akumulasi waktu eksekusi task / (jumlah worker x makespan) x 100. "
          "Ini utilisasi waktu kerja worker, bukan `Percentage CPU` Azure, dan tidak dibobot kapasitas CPU worker.",
          "- **Degree of Imbalance**: (sibuk maks - sibuk min) / rata-rata sibuk antarworker. "
          "Makin dekat 0 makin merata waktu sibuknya. Bukan ukuran jumlah task; lihat tabel jumlah task per worker.",
          ""]
    atomic_write(exp.report_path, "\n".join(L) + "\n")


# ---------------------------------------------------------------- verifikasi ulang dari file
def verify_experiment(exp, dataset_path=None):
    """Hitung ulang metrik dari tasks/*.csv dan bandingkan dengan ringkasan.csv."""
    cfg = exp.load_config()
    workers = cfg["workers"]
    price = cfg.get("host_price_per_hour", {})
    problems, checked = [], 0
    ds_path = dataset_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset",
                                           cfg["dataset"]["file"])
    if not os.path.exists(ds_path) or dataset_info(ds_path)["sha256"] != cfg["dataset"]["sha256"]:
        ds_path = None
    rows = read_csv(exp.summary_path)
    seen = set()
    for row in rows:
        n, run = int(row["tasks"]), int(row["run"])
        if (n, run) in seen:
            problems.append(f"{n}x run{run}: muncul lebih dari sekali di ringkasan.csv")
        seen.add((n, run))
        path = exp.task_file(n, run)
        if not os.path.exists(path):
            problems.append(f"{n}x run{run}: file {os.path.basename(path)} tidak ada")
            continue
        recs = []
        for r in read_csv(path):
            recs.append({"task_id": int(r["task_id"]), "length_mi": int(r["length_mi"]), "vm": r["vm"],
                         "host": r["host"], "type": r["type"], "start": float(r["start"]),
                         "finish": float(r["finish"]), "exec_time": float(r["exec_time"]),
                         "dataset_row": int(r["dataset_row"]), "status": r["status"]})
        if ds_path is None:
            problems.append(f"{n}x run{run}: dataset {cfg['dataset']['file']} dengan sha256 yang sama tidak ditemukan")
            continue
        tasks = load_gocj(ds_path, n)
        makespan = max(r["finish"] for r in recs) if recs else float("nan")
        p = validate_run(tasks, recs, makespan, workers)
        m = compute_metrics(recs, makespan, workers, price) if recs else None
        if m:
            for k in ("makespan", "cost", "ru", "di"):
                a, b = float(row[k]), m[k]
                if abs(a - b) > 1e-9 * max(1.0, abs(b)):
                    p.append(f"{k} di ringkasan {a} != hitung ulang {b}")
            for w in workers:
                if int(row[f"task_{w['name']}"]) != m["count"][w["name"]]:
                    p.append(f"jumlah task {w['name']} tidak cocok")
        problems += [f"{n}x run{run}: {x}" for x in p]
        checked += 1
    return checked, problems


# ---------------------------------------------------------------- main
def build_plan(sizes, runs, order, run_ids):
    run_list = [r for r in range(1, runs + 1) if (not run_ids or r in run_ids)]
    if order == "run":
        return [(n, r) for r in run_list for n in sizes]
    return [(n, r) for n in sizes for r in run_list]


def print_status(exp):
    cfg = exp.load_config()
    done = exp.done_runs()
    planned = [(n, r) for n in cfg["sizes"] for r in range(1, cfg["runs"] + 1)]
    print(f"Eksperimen {cfg['name']} ({exp.dir})")
    print(f"  {len(done & set(planned))} dari {len(planned)} run selesai")
    for r in range(1, cfg["runs"] + 1):
        line = " ".join(("[x]" if (n, r) in done else "[ ]") + str(n) for n in cfg["sizes"])
        print(f"  run {r}: {line}")


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="Scheduler OLB real world (mode sekuensial)")
    ap.add_argument("--workers", help="file JSON daftar worker")
    ap.add_argument("--dataset", default=os.path.join(here, "dataset", "GoCJ_Dataset_1000.csv"))
    ap.add_argument("--sizes", type=int, nargs="+", help="jumlah task, misal 100 200 300 (default 100)")
    ap.add_argument("--runs", type=int, help="jumlah pengulangan per ukuran (default 1)")
    ap.add_argument("--run-ids", type=int, nargs="+",
                    help="hanya jalankan nomor run ini pada sesi sekarang, misal --run-ids 1")
    ap.add_argument("--order", choices=["run", "size"], default=None,
                    help="run: run 1 semua ukuran, lalu run 2, dst. (default); size: 100x3, 200x3, dst.")
    ap.add_argument("--out", default=os.path.join(here, "hasil"), help="folder induk hasil")
    ap.add_argument("--name", help="nama eksperimen (default: waktu sekarang)")
    ap.add_argument("--resume", action="store_true", help="lanjutkan eksperimen yang sudah ada")
    ap.add_argument("--expect-iter", type=int, default=DEFAULT_EXPECT_ITER,
                    help="ITER_PER_MI yang wajib dipakai semua worker (default 20)")
    ap.add_argument("--task-timeout", type=float, default=600, help="batas waktu satu task (detik)")
    ap.add_argument("--note", default="", help="catatan sesi, misal sisa CPU credits")
    ap.add_argument("--check", action="store_true", help="cek semua worker bisa dihubungi, lalu keluar")
    ap.add_argument("--status", action="store_true", help="tampilkan progres eksperimen, lalu keluar")
    ap.add_argument("--verifikasi", action="store_true",
                    help="hitung ulang metrik dari file task dan cocokkan dengan ringkasan, lalu keluar")
    args = ap.parse_args(argv)

    # ---- perintah yang tidak menghubungi worker
    if args.status or args.verifikasi:
        if not args.name:
            ap.error("--status/--verifikasi butuh --name")
        exp = Experiment(args.out, args.name)
        if not exp.exists():
            raise SystemExit(f"Eksperimen tidak ditemukan: {exp.dir}")
        if args.status:
            print_status(exp)
        if args.verifikasi:
            checked, problems = verify_experiment(exp, args.dataset)
            if problems:
                print(f"VERIFIKASI GAGAL ({checked} run diperiksa):")
                for p in problems:
                    print("  -", p)
                return 1
            print(f"VERIFIKASI OK: {checked} run cocok dengan file task-nya.")
        return 0

    if not args.workers:
        ap.error("--workers wajib diisi")
    with open(args.workers, encoding="utf-8") as f:
        cfg_workers = json.load(f)
    workers, host_price = cfg_workers["workers"], cfg_workers.get("host_price_per_hour", {})

    print("Cek koneksi ke semua worker:")
    infos, problems = check_workers(workers, args.expect_iter)
    if problems:
        print("\nTIDAK SIAP:")
        for p in problems:
            print("  -", p)
        return 2
    print(f"Semua {len(workers)} worker siap, ITER_PER_MI = {args.expect_iter}.")
    if args.check:
        return 0

    # ---- siapkan folder eksperimen
    name = args.name or datetime.now().strftime("%Y%m%d-%H%M%S")
    exp = Experiment(args.out, name)
    ds = dataset_info(args.dataset)
    if exp.exists():
        if not args.resume:
            print(f"Eksperimen '{name}' sudah ada di {exp.dir}.\n"
                  f"Pakai --resume untuk melanjutkan, atau --name lain untuk eksperimen baru.")
            return 2
        cfg = exp.load_config()
        mismatch = []
        if cfg["dataset"]["sha256"] != ds["sha256"]:
            mismatch.append("isi dataset berbeda")
        if int(cfg["iter_per_mi"]) != args.expect_iter:
            mismatch.append(f"ITER_PER_MI {cfg['iter_per_mi']} != {args.expect_iter}")
        if [(w["name"], w["host"]) for w in cfg["workers"]] != [(w["name"], w["host"]) for w in workers]:
            mismatch.append("daftar worker berbeda")
        if args.sizes and args.sizes != cfg["sizes"]:
            mismatch.append(f"--sizes {args.sizes} != {cfg['sizes']}")
        if args.runs and args.runs != cfg["runs"]:
            mismatch.append(f"--runs {args.runs} != {cfg['runs']}")
        if args.order and args.order != cfg["order"]:
            mismatch.append(f"--order {args.order} != {cfg['order']}")
        if mismatch:
            print("Tidak bisa melanjutkan, konfigurasi berbeda dari eksperimen awal:")
            for m in mismatch:
                print("  -", m)
            return 2
        if host_price != cfg.get("host_price_per_hour", {}):
            print("Tidak bisa melanjutkan: tarif host di file worker berbeda dari eksperimen awal.")
            return 2
    else:
        if args.resume:
            print(f"Tidak ada eksperimen '{name}' untuk dilanjutkan ({exp.dir}).")
            return 2
        cfg = {
            "schema_version": SCHEMA_VERSION, "name": name, "sampling": SAMPLING,
            "created_at": now_iso(), "dataset": ds, "iter_per_mi": args.expect_iter,
            "sizes": args.sizes or [100], "runs": args.runs or 1, "order": args.order or "run",
            "task_timeout_s": args.task_timeout,
            "workers_file": os.path.basename(args.workers),
            "workers": [{**w, "type": infos[w["name"]].get("type"), "cpus": infos[w["name"]].get("cpus")}
                        for w in workers],
            "host_price_per_hour": host_price,
            "scheduler_host": socket.gethostname(), "python": platform.python_version(),
        }
        exp.create(cfg)

    for n in cfg["sizes"]:
        if n > ds["rows"]:
            raise SystemExit(f"Ukuran {n} melebihi jumlah baris dataset ({ds['rows']}).")
    bad_ids = [r for r in (args.run_ids or []) if not 1 <= r <= cfg["runs"]]
    if bad_ids:
        raise SystemExit(f"--run-ids {bad_ids} di luar 1..{cfg['runs']}")

    plan = build_plan(cfg["sizes"], cfg["runs"], cfg["order"], args.run_ids)
    done = exp.done_runs()
    todo = [p for p in plan if p not in done]
    sessions = read_csv(exp.session_path)
    session_no = len(sessions) + 1
    seq_no = len(read_csv(exp.summary_path))
    session_start = now_iso()

    print(f"\nEksperimen : {name}  ->  {exp.dir}")
    print(f"Sesi       : {session_no}")
    print(f"Rencana    : {len(plan)} run pada sesi ini, {len(plan) - len(todo)} sudah selesai sebelumnya, "
          f"{len(todo)} akan dijalankan")
    if args.note:
        print(f"Catatan    : {args.note}")

    fields = summary_fields(workers)
    baseline = {}
    for p in read_csv(exp.perf_path):
        if p["detik_per_juta_iterasi"] and p["worker"] not in baseline:
            baseline[p["worker"]] = float(p["detik_per_juta_iterasi"])

    finished, status, exit_code = 0, None, 0
    try:
        for n, run in todo:
            # cek cepat sebelum tiap run, supaya run tidak dimulai dengan worker yang mati
            infos_now, problems = check_workers(workers, cfg["iter_per_mi"], verbose=False)
            if problems:
                print(f"\n[STOP] Worker tidak siap sebelum {n} task run {run}:")
                for p in problems:
                    print("  -", p)
                print("  (Kalau worker masih mengerjakan task lama setelah timeout, tunggu sebentar.)")
                exp.log_failure(n, run, ["cek /info sebelum run: " + p for p in problems], [])
                status, exit_code = "berhenti: worker tidak siap", 2
                break
            infos = infos_now

            orphan = exp.quarantine_orphan(n, run)
            if orphan:
                print(f"  File task {n}x run{run} tanpa baris ringkasan dipindah ke {orphan}")

            tasks = load_gocj(args.dataset, n)
            started = now_iso()
            print(f"\n[RUN] {n} task, run {run}/{cfg['runs']}  (mulai {started}) ...", flush=True)
            try:
                records, makespan = run_olb(tasks, workers, task_timeout=cfg.get("task_timeout_s", 600),
                                            iter_per_mi=cfg["iter_per_mi"])
                problems = validate_run(tasks, records, makespan, workers)
                m = compute_metrics(records, makespan, workers, host_price) if not problems else None
                if m:
                    problems += validate_metrics(m, records, workers)
                if problems:
                    raise RunFailed(["validasi: " + p for p in problems], records)
            except RunFailed as e:
                partial = exp.log_failure(n, run, e.errors, e.records)
                print(f"  RUN GAGAL ({len(e.errors)} masalah), tidak dimasukkan ke ringkasan:")
                for err in e.errors[:10]:
                    print("    -", err)
                print(f"  Detail: {exp.fail_log}" + (f"\n  Task yang sempat selesai: {partial}" if partial else ""))
                status, exit_code = f"berhenti: {n}x run{run} gagal", 2
                break
            finished_at = now_iso()

            # 1) file task (tidak pernah menimpa)  2) ringkasan  3) kinerja  4) laporan
            for r in records:
                r.setdefault("iterations", r["length_mi"] * cfg["iter_per_mi"])
            write_new_csv(exp.task_file(n, run), [{k: r.get(k, "") for k in TASK_FIELDS} for r in records])
            seq_no += 1
            row = {"tasks": n, "run": run, **{k: m[k] for k in ("makespan", "cost", "ru", "di")},
                   **{f"task_{vm}": c for vm, c in m["count"].items()},
                   "sampling": SAMPLING, "dataset": ds["file"], "iter_per_mi": cfg["iter_per_mi"],
                   "total_mi": sum(t["length"] for t in tasks), "urutan": seq_no, "sesi": session_no,
                   "waktu_mulai": started, "waktu_selesai": finished_at, "status": "ok"}
            append_csv_row(exp.summary_path, fields, row)
            perf_rows = worker_perf(records, workers, infos, cfg["iter_per_mi"])
            for p in perf_rows:
                append_csv_row(exp.perf_path, PERF_FIELDS, {"tasks": n, "run": run, **p})
            build_report(exp)
            finished += 1

            print(f"  Makespan = {m['makespan']:.2f} s | Est. Cost = ${m['cost']:.6f} | "
                  f"RU = {m['ru']:.2f}% | DI = {m['di']:.4f}   [tersimpan, validasi OK]")
            for p in perf_rows:
                spm = p["detik_per_juta_iterasi"]
                note = ""
                if spm != "" and p["worker"] in baseline and baseline[p["worker"]] > 0:
                    change = (spm / baseline[p["worker"]] - 1) * 100
                    if change > SLOWDOWN_NOTE_PCT:
                        note = f"  <- {change:.0f}% lebih lambat dari run pertama (indikasi, cek Azure Monitor)"
                elif spm != "":
                    baseline.setdefault(p["worker"], spm)
                print(f"    {p['worker']:<14} task={p['jumlah_task']:>4}  busy={p['busy_s']:8.2f} s  "
                      f"s/juta-iter={spm if spm != '' else '-'}{note}")
            if any(p["jumlah_task"] == 0 for p in perf_rows):
                print("  CATATAN: ada worker yang tidak mendapat task pada run ini.")
        if status is None:
            status = "selesai"
    except KeyboardInterrupt:
        status, exit_code = "dihentikan pengguna (Ctrl+C)", 130
        print("\nDihentikan. Run yang sedang berjalan tidak disimpan; run sebelumnya aman.")
    finally:
        status = status or "error tak terduga (lihat pesan di terminal)"
        append_csv_row(exp.session_path, SESSION_FIELDS, {
            "sesi": session_no, "mulai": session_start, "selesai": now_iso(),
            "run_diminta": " ".join(f"{n}x{r}" for n, r in plan),
            "run_selesai": finished, "status": status, "catatan": args.note})
        if os.path.exists(exp.summary_path):
            build_report(exp)

    print(f"\nSesi {session_no}: {finished} run baru tersimpan. Status: {status}.")
    print(f"Hasil: {exp.dir}")
    if exit_code == 2:
        print(f"Setelah masalah diperbaiki, lanjutkan dengan:  --name {name} --resume")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
