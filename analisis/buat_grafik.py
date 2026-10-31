"""
Membuat grafik hasil pengujian sekuensial Azure dan perbandingannya dengan CloudSim.

Pakai:
    pip install matplotlib
    python analisis/buat_grafik.py hasil-azure/sequential/azure-seq-30run

Input  : ringkasan.csv dan kinerja_worker.csv di folder hasil eksperimen.
Output : file PNG di folder grafik/ (di root repo).

Data CloudSim diambil dari HASIL_PENGUJIAN.md repo CloudSim kelompok 8, bagian 1.2
(pendekatan trace-driven / sekuensial, rata-rata 3 run yang identik).
"""
import csv
import os
import statistics
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# ---- data CloudSim (HASIL_PENGUJIAN.md, bagian 1.2 Trace-Driven) ----
CLOUDSIM = {
    # tasks: (makespan s, total cost $, RU %, DI)
    100: (17078.41, 5541.19, 48.09, 1.8122),
    200: (68599.70, 23585.50, 46.64, 1.9292),
    300: (180452.95, 56043.04, 42.38, 2.0365),
    400: (365392.41, 108063.04, 39.48, 2.3621),
    500: (604126.77, 175949.00, 38.48, 2.4258),
    600: (999698.09, 284846.28, 35.89, 2.6162),
    700: (1219718.22, 383690.51, 39.50, 2.2913),
    800: (1555496.54, 485351.93, 39.68, 2.2928),
    900: (1817028.95, 573980.85, 41.33, 2.1033),
    1000: (2278486.77, 727760.54, 43.23, 1.9416),
}

# ---- gaya (palet kategorikal tervalidasi, latar terang) ----
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
C_AZURE, C_CLOUDSIM, C_THIRD = "#2a78d6", "#eb6834", "#1baf7a"
plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True,
    "axes.spines.top": False, "axes.spines.right": False, "font.size": 10,
    "axes.titlesize": 11, "axes.titleweight": "bold", "axes.titlecolor": INK,
    "legend.frameon": False, "lines.linewidth": 2,
})


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def per_size(rows, key):
    by = {}
    for r in rows:
        by.setdefault(int(r["tasks"]), []).append(float(r[key]))
    sizes = sorted(by)
    mean = [statistics.mean(by[n]) for n in sizes]
    sd = [statistics.stdev(by[n]) if len(by[n]) > 1 else 0 for n in sizes]
    return sizes, mean, sd, by


def save(fig, out, name):
    path = os.path.join(out, name)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("  ", path)


def azure_panel(ax, rows, key, title, ylabel, fmt=None):
    sizes, mean, sd, by = per_size(rows, key)
    for n in sizes:
        ax.scatter([n] * len(by[n]), by[n], s=14, color=C_AZURE, alpha=0.35, zorder=3, linewidths=0)
    ax.errorbar(sizes, mean, yerr=sd, color=C_AZURE, marker="o", markersize=5, capsize=3,
                elinewidth=1, zorder=4, label="Azure (rata-rata ± SD, titik = tiap run)")
    ax.set_title(title, loc="left")
    ax.set_xlabel("Jumlah task")
    ax.set_ylabel(ylabel)
    ax.set_xticks(sizes)
    if fmt:
        ax.yaxis.set_major_formatter(fmt)


def cloudsim_panel(ax, idx, title, ylabel, fmt=None):
    sizes = sorted(CLOUDSIM)
    ax.plot(sizes, [CLOUDSIM[n][idx] for n in sizes], color=C_CLOUDSIM, marker="s", markersize=5)
    ax.set_title(title, loc="left")
    ax.set_xlabel("Jumlah task")
    ax.set_ylabel(ylabel)
    ax.set_xticks(sizes)
    if fmt:
        ax.yaxis.set_major_formatter(fmt)


def main(result_dir, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    rows = read_csv(os.path.join(result_dir, "ringkasan.csv"))
    perf_path = os.path.join(result_dir, "kinerja_worker.csv")
    perf = read_csv(perf_path) if os.path.exists(perf_path) else []
    thousands = matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}".replace(",", "."))
    print("Grafik dibuat:")

    # 1. Makespan: dua panel (skala dan satuan model berbeda, jadi tidak dijadikan satu sumbu)
    fig, (a, b) = plt.subplots(1, 2, figsize=(11, 4))
    azure_panel(a, rows, "makespan", "Azure (real world)", "Makespan (detik)")
    cloudsim_panel(b, 0, "CloudSim (simulasi)", "Makespan (detik simulasi)", thousands)
    fig.suptitle("Makespan OLB sekuensial terhadap jumlah task", x=0.01, ha="left",
                 fontweight="bold", color=INK)
    fig.tight_layout()
    save(fig, out_dir, "grafik_makespan.png")

    # 2. Cost: dua panel (model biaya berbeda)
    fig, (a, b) = plt.subplots(1, 2, figsize=(11, 4))
    azure_panel(a, rows, "cost", "Azure: Estimated Compute Cost", "USD (2 host x makespan)")
    cloudsim_panel(b, 1, "CloudSim: Total Cost", "$ (tarif per tipe VM x waktu eksekusi)", thousands)
    fig.suptitle("Biaya terhadap jumlah task (model biaya berbeda, bandingkan polanya)", x=0.01,
                 ha="left", fontweight="bold", color=INK)
    fig.tight_layout()
    save(fig, out_dir, "grafik_cost.png")

    # 3 & 4. RU dan DI: satuan sama, boleh satu sumbu
    for key, idx, title, ylabel, name, ylim in (
            ("ru", 2, "Resource Utilization: Azure vs CloudSim", "RU (%)", "grafik_ru.png", (0, 105)),
            ("di", 3, "Degree of Imbalance: Azure vs CloudSim", "DI (0 = merata)", "grafik_di.png", None)):
        sizes, mean, sd, _ = per_size(rows, key)
        cs = [CLOUDSIM[n][idx] for n in sizes]
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.errorbar(sizes, mean, yerr=sd, color=C_AZURE, marker="o", markersize=5, capsize=3,
                    elinewidth=1, label="Azure (rata-rata ± SD)")
        ax.plot(sizes, cs, color=C_CLOUDSIM, marker="s", markersize=5, label="CloudSim")
        ax.annotate("Azure", (sizes[-1], mean[-1]), xytext=(8, 0), textcoords="offset points",
                    color=INK2, va="center")
        ax.annotate("CloudSim", (sizes[-1], cs[-1]), xytext=(8, 0), textcoords="offset points",
                    color=INK2, va="center")
        ax.set_title(title, loc="left")
        ax.set_xlabel("Jumlah task")
        ax.set_ylabel(ylabel)
        ax.set_xticks(sizes)
        if ylim:
            ax.set_ylim(*ylim)
        else:
            ax.set_ylim(bottom=0)
        ax.legend(loc="lower left" if key == "ru" else "center right")
        fig.tight_layout()
        save(fig, out_dir, name)

    # 5. Rata-rata jumlah task per worker menurut tipe
    types = (("large", "Large (1,0 CPU)", C_AZURE), ("medium", "Medium (0,5 CPU)", C_CLOUDSIM),
             ("small", "Small (0,25 CPU)", C_THIRD))
    sizes = sorted({int(r["tasks"]) for r in rows})
    cols = [k for k in rows[0] if k.startswith("task_")]
    fig, ax = plt.subplots(figsize=(10, 4))
    width = 26
    for i, (t, label, color) in enumerate(types):
        tcols = [c for c in cols if c.endswith(t)]
        vals = []
        for n in sizes:
            rs = [r for r in rows if int(r["tasks"]) == n]
            vals.append(statistics.mean(statistics.mean(float(r[c]) for c in tcols) for r in rs))
        ax.bar([n + (i - 1) * (width + 2) for n in sizes], vals, width=width, color=color, label=label)
    ax.set_title("Rata-rata jumlah task per worker menurut tipe (Azure)", loc="left")
    ax.set_xlabel("Jumlah task")
    ax.set_ylabel("Task per worker")
    ax.set_xticks(sizes)
    ax.legend(loc="upper left")
    fig.tight_layout()
    save(fig, out_dir, "grafik_task_per_tipe.png")

    # 6. Kestabilan kecepatan worker sepanjang 30 run (indikator, bukan bukti throttling)
    if perf:
        order = {(r["tasks"], r["run"]): int(r["urutan"]) for r in rows}
        sesi = {int(r["urutan"]): int(r["sesi"]) for r in rows}
        fig, ax = plt.subplots(figsize=(10, 4))
        for t, label, color in types:
            pts = {}
            for p in perf:
                if p["worker"].endswith(t) and p["detik_per_juta_iterasi"]:
                    pts.setdefault(order[(p["tasks"], p["run"])], []).append(float(p["detik_per_juta_iterasi"]))
            xs = sorted(pts)
            ax.plot(xs, [statistics.mean(pts[x]) for x in xs], color=color, marker="o", markersize=4,
                    label=label)
        for s in sorted(set(sesi.values())):
            xs = [u for u, v in sesi.items() if v == s]
            ax.axvspan(min(xs) - 0.5, max(xs) + 0.5, color=GRID, alpha=0.35 if s % 2 else 0.0, lw=0)
            ax.text((min(xs) + max(xs)) / 2, 1.0, f"Sesi {s}", transform=ax.get_xaxis_transform(),
                    ha="center", va="bottom", color=INK2)
        ax.set_title("Kecepatan worker sepanjang 30 run (makin kecil makin cepat)", loc="left", pad=18)
        ax.set_xlabel("Urutan eksekusi run")
        ax.set_ylabel("Detik per 1 juta iterasi")
        ax.set_ylim(bottom=0)
        ax.legend(loc="center right")
        fig.tight_layout()
        save(fig, out_dir, "grafik_kecepatan_worker.png")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("Pakai: python analisis/buat_grafik.py <folder-hasil-eksperimen> [folder-output]")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else os.path.join(root, "grafik"))
