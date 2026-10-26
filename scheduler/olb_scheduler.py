"""
Scheduler OLB (Opportunistic Load Balancing) versi real world.

Ide OLB: setiap task diberikan ke VM yang PALING AWAL SIAP (ready time terkecil),
tanpa melihat seberapa cepat VM itu. Sama dengan applyOlbScheduling di CloudSim.

Bedanya dengan CloudSim:
  - Di CloudSim, ready time DIHITUNG (length / MIPS).
  - Di sini, ready time DIAMATI: VM dianggap siap saat ia benar-benar selesai
    mengerjakan task sebelumnya dan melapor balik lewat jaringan.

Contoh pakai:
  python3 olb_scheduler.py --workers workers.local.json --check
  python3 olb_scheduler.py --workers workers.local.json --sizes 100 --runs 1
  python3 olb_scheduler.py --workers workers.azure.json --sizes 100 200 300 --runs 3

Hanya pakai library bawaan Python.
"""
import argparse
import csv
import json
import os
import statistics
import threading
import time
import urllib.request
from datetime import datetime


# ---------------------------------------------------------------- utilitas jaringan
def http_get(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def http_post(url, payload, timeout=3600):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


# ---------------------------------------------------------------- dataset
def load_gocj(path, n):
    """Ambil n task pertama dari file GoCJ (angka pertama tiap baris = panjang task dalam MI)."""
    lengths = []
    with open(path) as f:
        for line in f:
            first = line.strip().replace(";", ",").split(",")[0].strip()
            if first.replace(".", "", 1).isdigit():
                lengths.append(int(float(first)))
            if len(lengths) == n:
                break
    if len(lengths) < n:
        raise SystemExit(f"Dataset {path} cuma punya {len(lengths)} task, diminta {n}.")
    return [{"task_id": i, "length": L} for i, L in enumerate(lengths)]


# ---------------------------------------------------------------- inti OLB
def run_olb(tasks, workers):
    names = [w["name"] for w in workers]
    url = {w["name"]: w["url"] for w in workers}
    order = {n: i for i, n in enumerate(names)}

    ready_time = {n: 0.0 for n in names}   # kapan VM terakhir kali jadi siap (detik sejak mulai)
    idle = set(names)                      # VM yang sedang menganggur
    cond = threading.Condition()
    records, errors, threads = [], [], []
    t0 = time.perf_counter()

    def dispatch(vm, task):
        sent = time.perf_counter() - t0
        try:
            resp = http_post(url[vm] + "/task", task)
        except Exception as e:  # noqa: BLE001
            with cond:
                errors.append(f"{vm} gagal mengerjakan task {task['task_id']}: {e}")
                idle.add(vm)
                cond.notify_all()
            return
        done = time.perf_counter() - t0
        with cond:
            records.append({
                "task_id": task["task_id"], "length_mi": task["length"], "vm": vm,
                "host": resp.get("host"), "type": resp.get("type"),
                "start": round(sent, 4), "finish": round(done, 4),
                "exec_time": round(resp["exec_time"], 4),
            })
            ready_time[vm] = done      # VM ini siap lagi SEKARANG
            idle.add(vm)
            cond.notify_all()

    for task in tasks:
        with cond:
            while not idle:
                cond.wait()
            if errors:
                break
            # ===== LANGKAH OLB: pilih VM dengan ready time paling awal =====
            vm = min(idle, key=lambda n: (ready_time[n], order[n]))
            idle.remove(vm)
        th = threading.Thread(target=dispatch, args=(vm, task), daemon=True)
        th.start()
        threads.append(th)

    for th in threads:
        th.join()
    if errors:
        raise SystemExit("ERROR:\n  " + "\n  ".join(errors))

    makespan = max(r["finish"] for r in records)
    return sorted(records, key=lambda r: r["task_id"]), makespan


# ---------------------------------------------------------------- metrik
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
    # Biaya nyata: semua host menyala selama makespan
    cost = sum(host_price.values()) * makespan / 3600
    return {
        "makespan": makespan, "cost": cost, "ru": ru, "di": di,
        "busy": busy, "count": count,
    }


# ---------------------------------------------------------------- main
def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="Scheduler OLB real world")
    ap.add_argument("--workers", required=True, help="file JSON daftar VM")
    ap.add_argument("--dataset", default=os.path.join(here, "dataset", "GoCJ_Dataset_1000.csv"))
    ap.add_argument("--sizes", type=int, nargs="+", default=[100])
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--out", default=os.path.join(here, "hasil"))
    ap.add_argument("--check", action="store_true", help="cek semua VM bisa dihubungi, lalu keluar")
    args = ap.parse_args()

    cfg = json.load(open(args.workers))
    workers, host_price = cfg["workers"], cfg.get("host_price_per_hour", {})

    print("Cek koneksi ke semua VM:")
    for w in workers:
        try:
            info = http_get(w["url"] + "/info")
            print(f"  OK   {w['name']:<14} host={info['host']:<6} tipe={info['type']:<7} cpus={info['cpus']}")
        except Exception as e:  # noqa: BLE001
            raise SystemExit(f"  GAGAL {w['name']} ({w['url']}): {e}")
    if args.check:
        return

    os.makedirs(args.out, exist_ok=True)
    summary = []
    for n in args.sizes:
        tasks = load_gocj(args.dataset, n)
        for run in range(1, args.runs + 1):
            print(f"\n[RUN] {n} task, run {run}/{args.runs} ...", flush=True)
            records, makespan = run_olb(tasks, workers)
            m = compute_metrics(records, makespan, workers, host_price)

            with open(os.path.join(args.out, f"tasks_{n}_run{run}.csv"), "w", newline="") as f:
                wr = csv.DictWriter(f, fieldnames=list(records[0].keys()))
                wr.writeheader()
                wr.writerows(records)

            print(f"  Makespan = {m['makespan']:.2f} s | Cost = ${m['cost']:.5f} | "
                  f"RU = {m['ru']:.2f}% | DI = {m['di']:.4f}")
            for vm in m["busy"]:
                print(f"    {vm:<14} task={m['count'][vm]:>4}  busy={m['busy'][vm]:8.2f} s")
            summary.append({"tasks": n, "run": run, **{k: m[k] for k in ("makespan", "cost", "ru", "di")},
                            **{f"task_{vm}": c for vm, c in m["count"].items()}})

    # simpan ringkasan
    with open(os.path.join(args.out, "ringkasan.csv"), "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(summary[0].keys()))
        wr.writeheader()
        wr.writerows(summary)

    lines = [
        "# Hasil Pengujian OLB Real World", "",
        f"- Waktu: {datetime.now():%Y-%m-%d %H:%M}",
        f"- Dataset: {os.path.basename(args.dataset)}",
        f"- Infrastruktur: {len({w['host'] for w in workers})} host, {len(workers)} VM",
        "", "## Rata-rata per jumlah task", "",
        "| Jumlah Task | Makespan (s) | Cost ($) | Resource Utilization (%) | Degree of Imbalance |",
        "|---:|---:|---:|---:|---:|",
    ]
    for n in args.sizes:
        rows = [s for s in summary if s["tasks"] == n]
        avg = {k: statistics.mean(r[k] for r in rows) for k in ("makespan", "cost", "ru", "di")}
        lines.append(f"| {n} | {avg['makespan']:.2f} | {avg['cost']:.5f} | {avg['ru']:.2f} | {avg['di']:.4f} |")
    lines += ["", "## Detail tiap run", "",
              "| Jumlah Task | Run | Makespan (s) | Cost ($) | RU (%) | DI |", "|---:|---:|---:|---:|---:|---:|"]
    for s in summary:
        lines.append(f"| {s['tasks']} | {s['run']} | {s['makespan']:.2f} | {s['cost']:.5f} | {s['ru']:.2f} | {s['di']:.4f} |")
    with open(os.path.join(args.out, "HASIL_REALWORLD.md"), "w") as f:
        f.write("\n".join(lines) + "\n")

    print(f"\nSelesai. Hasil tersimpan di folder: {args.out}")


if __name__ == "__main__":
    main()
