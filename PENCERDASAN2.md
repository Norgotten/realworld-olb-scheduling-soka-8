# PENCERDASAN 2: Cara Kerja Kode

Penjelasan detail tiap file dan konsep di balik proyek ini. Cara setup ada di
[PENCERDASAN.md](PENCERDASAN.md).

---

## 1. Alur kerja dari awal sampai akhir

1. **Scheduler membaca dataset.** Ambil N baris pertama GoCJ, tiap baris = panjang satu
   task dalam MI. Contoh baris pertama: `83000`.
2. **Scheduler mencari VM yang paling awal siap** (langkah OLB, bagian 4).
3. **Scheduler mengirim task ke VM itu** lewat jaringan: `POST http://<ip>:<port>/task`
   dengan isi `{"task_id": 0, "length": 83000}`.
4. **VM mengerjakan task**: memutar loop sebanyak `length x ITER_PER_MI` kali.
   Karena jatah CPU tiap VM berbeda, VM Large selesai lebih cepat daripada VM Small.
5. **VM membalas** dengan lama eksekusi, misalnya `{"exec_time": 0.29, ...}`.
6. **Scheduler mencatat** jam mulai dan jam selesai, lalu menandai VM itu siap lagi.
7. Langkah 2 sampai 6 diulang sampai semua task habis.
8. **Scheduler menghitung metrik** (makespan, cost, RU, DI) dan menyimpan hasil.

Pemetaan dari CloudSim:

| Konsep | Di CloudSim | Di proyek ini |
|---|---|---|
| Datacenter | `DatacenterSimple` | Resource Group Azure |
| Host | `HostSimple` | VM Azure B2als_v2 (2 vCPU, 4 GB) |
| VM | `VmSimple` dengan MIPS | Container Docker dengan batas CPU |
| Cloudlet | `CloudletSimple` dengan length (MI) | Task berisi length (MI) dikirim lewat jaringan |
| Eksekusi task | Dihitung: `length / MIPS` | Dijalankan: CPU benar-benar bekerja |
| Scheduler | `applyOlbScheduling()` | `scheduler/olb_scheduler.py` |

Kenapa VM-nya container, bukan VM Azure langsung? Di Azure kita tidak bisa memilih server
fisik tempat VM ditaruh, jadi "host" tidak bisa ditunjukkan. Solusinya, VM Azure dijadikan
host, lalu di dalamnya dijalankan beberapa container (virtualisasi ringan) sebagai VM.

---

## 2. Konsep yang perlu dipahami

### 2.1 Dari MI ke kerja nyata (`ITER_PER_MI`)

MI (Million Instructions) adalah ukuran "berat" task. Di CloudSim, durasi task cukup
dihitung: `length / MIPS`. Di dunia nyata kita tidak bisa menyuruh CPU menjalankan
"tepat 83.000 juta instruksi", jadi dibuat **pekerjaan yang sebanding** dengan MI:

```python
# worker/worker.py
def burn_cpu(iterations):
    x = 0
    for i in range(iterations):
        x = (x + i * i) % 1_000_003   # hitungan kecil, tujuannya hanya membuat CPU sibuk
    return x

burn_cpu(length * ITER_PER_MI)        # ITER_PER_MI = 20
```

Contoh: task 83.000 MI dikerjakan dengan 83.000 x 20 = **1.660.000 putaran loop**.
Task 166.000 MI jadi 3.320.000 putaran, dua kali lebih lama. Jadi:

- **Perbandingan berat antar task tetap sama** dengan dataset.
- Angka 20 adalah **faktor skala** supaya satu ujicoba selesai dalam detik, bukan jam.
- Satu putaran loop **bukan** satu instruksi CPU. Yang dijaga hanya proporsinya.
- Faktor ini bisa diubah lewat variabel `ITER_PER_MI` tanpa mengubah kode.

### 2.2 Jatah CPU = kecepatan VM

Di CloudSim kecepatan VM diatur lewat MIPS. Di sini lewat jatah CPU Docker:

```bash
docker run --cpus 1.0  ...   # Large : boleh pakai 100% dari 1 core
docker run --cpus 0.5  ...   # Medium: maksimal 50% dari 1 core
docker run --cpus 0.25 ...   # Small : maksimal 25% dari 1 core
```

Loop yang sama di VM Small kira-kira butuh waktu 4x lebih lama daripada di VM Large.

### 2.3 Kenapa ada port

Tiap VM adalah **program server kecil** yang menunggu kiriman task. Supaya scheduler bisa
mengirim ke VM tertentu, VM itu butuh **alamat**: IP + port.

Analogi: **IP = alamat gedung (host), port = nomor kamar (VM)**.

- `172.16.0.5:5001` = gedung host2, kamar 5001 = VM host2-large
- `172.16.0.5:5003` = gedung host2, kamar 5003 = VM host2-small

Di dalam container, worker selalu mendengarkan di port 5000. Docker menyambungkan port
host ke port container:

```bash
-p 5001:5000    # pintu 5001 di host diteruskan ke pintu 5000 di dalam container Large
-p 5002:5000    # ke container Medium
-p 5003:5000    # ke container Small
```

Tiga VM dalam satu host tidak bisa memakai nomor pintu yang sama, makanya 5001, 5002, 5003.
host2 boleh memakai nomor yang sama karena IP-nya berbeda.

### 2.4 Cara scheduler dan VM berkomunikasi

Scheduler dan VM berbicara lewat HTTP, cara paling sederhana mengirim pesan antar komputer.
Worker hanya punya dua alamat:

| Alamat | Fungsi |
|---|---|
| `GET /info` | "Kamu siapa?" Dipakai `--check` untuk memastikan VM bisa dihubungi |
| `POST /task` | "Kerjakan task ini." Worker menjalankan loop lalu membalas lama eksekusinya |

Bisa dicoba saat uji di laptop: buka `http://localhost:5001/info` di browser.

### 2.5 Satu VM hanya mengerjakan satu task sekaligus

Worker memakai `HTTPServer` bawaan Python yang **single-threaded**: selama satu task
dikerjakan, permintaan lain menunggu. Ini sama dengan asumsi OLB di CloudSim bahwa task
antre di VM, dan membuat "VM siap" punya arti jelas: VM sedang tidak mengerjakan apa pun.

### 2.6 Docker: image dan container

- **Image** = cetakan, dibuat dari `worker/Dockerfile`.
- **Container** = hasil cetakan yang sedang berjalan. Satu image dipakai untuk 3 container
  dengan pengaturan berbeda (jatah CPU, port, nama).

---

## 3. Penjelasan tiap file

### 3.1 `worker/worker.py` (program VM)

Program yang berjalan di dalam tiap container.

| Bagian | Fungsi |
|---|---|
| `VM_NAME`, `HOST_NAME`, `VM_TYPE`, `VM_CPUS` | Identitas VM, dibaca dari variabel lingkungan yang diisi saat container dinyalakan |
| `ITER_PER_MI` | Faktor skala MI ke putaran loop (default 20) |
| `PORT` | Port yang didengarkan di dalam container (5000) |
| `burn_cpu()` | Loop yang membuat CPU sibuk, mewakili eksekusi task |
| `do_GET` | Menjawab `/info` dengan identitas VM |
| `do_POST` | Menerima `/task`, menjalankan `burn_cpu`, membalas `exec_time` |
| `log_message` | Dimatikan supaya terminal tidak penuh |

### 3.2 `worker/Dockerfile` (resep image)

```dockerfile
FROM python:3.12-slim                # mulai dari Linux kecil yang sudah ada Python
WORKDIR /app                         # folder kerja di dalam container
COPY worker.py .                     # masukkan program worker
EXPOSE 5000                          # worker mendengarkan di port 5000
CMD ["python", "-u", "worker.py"]    # perintah saat container dinyalakan
```

### 3.3 `worker/start_host.sh` (menyalakan 3 VM di satu host)

Dipanggil dengan `bash start_host.sh host1`. Langkahnya:

1. `docker build -t olb-worker .` membuat image dari Dockerfile.
2. Menjalankan 3 container lewat fungsi `run_vm TIPE CPU PORT`:

| VM | `--cpus` | Port host |
|---|---|---|
| LARGE | 1.0 | 5001 |
| MEDIUM | 0.5 | 5002 |
| SMALL | 0.25 | 5003 |

Opsi `docker run` yang dipakai:

| Opsi | Arti |
|---|---|
| `-d` | jalan di latar belakang |
| `--name host1-large` | nama container |
| `--restart unless-stopped` | otomatis nyala lagi saat host di-Start ulang |
| `--cpus 1.0` | jatah CPU (kecepatan VM) |
| `-p 5001:5000` | sambungan port (alamat VM) |
| `-e VM_NAME=... -e VM_TYPE=...` | identitas VM, dibaca oleh `worker.py` |
| `-e ITER_PER_MI=20` | faktor skala task |

Sebelum menyalakan, container lama dengan nama sama dihapus dulu (`docker rm -f`), jadi
script aman dijalankan berulang kali.

### 3.4 `docker-compose.yml` (khusus uji di laptop)

Menyalakan 6 container sekaligus di satu laptop untuk mensimulasikan 2 host x 3 VM.
Isinya sama dengan `start_host.sh`, bedanya port dibuat 5001 sampai 5006 karena semua
berada di satu mesin:

| Container | `cpus` | Port laptop |
|---|---|---|
| host1-large / medium / small | 1.0 / 0.5 / 0.25 | 5001 / 5002 / 5003 |
| host2-large / medium / small | 1.0 / 0.5 / 0.25 | 5004 / 5005 / 5006 |

### 3.5 `scheduler/workers.local.json` dan `scheduler/workers.azure.json` (daftar VM)

File ini memberi tahu scheduler **VM mana saja yang ada dan di mana alamatnya**.
Scheduler tidak menebak sendiri; semua VM harus terdaftar di sini.

Isi `workers.azure.json`:

```json
{
  "keterangan": "...",
  "host_price_per_hour": { "host1": 0.0428, "host2": 0.0428 },
  "workers": [
    { "name": "host1-large",  "host": "host1", "url": "http://localhost:5001" },
    { "name": "host1-medium", "host": "host1", "url": "http://localhost:5002" },
    { "name": "host1-small",  "host": "host1", "url": "http://localhost:5003" },
    { "name": "host2-large",  "host": "host2", "url": "http://172.16.0.5:5001" },
    { "name": "host2-medium", "host": "host2", "url": "http://172.16.0.5:5002" },
    { "name": "host2-small",  "host": "host2", "url": "http://172.16.0.5:5003" }
  ]
}
```

| Kolom | Arti |
|---|---|
| `keterangan` | Catatan untuk manusia, tidak dipakai program |
| `host_price_per_hour` | Harga sewa tiap host per jam (USD), dipakai menghitung Cost. Angka 0.0428 diambil dari halaman pembuatan VM Azure (B2als_v2, Indonesia Central) |
| `workers` | Daftar 6 VM |
| `name` | Nama VM, dipakai di output |
| `host` | Host tempat VM berada |
| `url` | Alamat VM = IP + port |

Kenapa VM host1 pakai `localhost`? Scheduler berjalan **di host1 itu sendiri**, dan
`localhost` artinya "komputer ini". VM host2 dihubungi lewat Private IP-nya (`172.16.0.5`)
melalui jaringan internal Azure.

Bedanya dengan `workers.local.json`: semua VM ada di laptop, jadi semua `localhost`
dengan port 5001 sampai 5006, dan harga diisi 0.

### 3.6 `scheduler/olb_scheduler.py` (scheduler OLB)

| Fungsi | Tugas |
|---|---|
| `http_get`, `http_post` | Mengirim pesan ke VM |
| `load_gocj(path, n)` | Membaca n task pertama dari dataset. Hanya angka pertama tiap baris yang dipakai (panjang MI) |
| `run_olb(tasks, workers)` | Inti algoritma OLB, dijelaskan di bagian 4 |
| `compute_metrics(...)` | Menghitung makespan, cost, RU, DI (bagian 5) |
| `main()` | Membaca opsi, cek koneksi semua VM, menjalankan ujicoba, menyimpan hasil |

Opsi perintah:

| Opsi | Arti |
|---|---|
| `--workers` | file daftar VM (`workers.local.json` atau `workers.azure.json`) |
| `--check` | hanya cek semua VM bisa dihubungi, lalu berhenti |
| `--sizes` | jumlah task yang diuji, bisa lebih dari satu, misal `100 200 300` |
| `--runs` | berapa kali tiap jumlah task diulang |
| `--dataset` | file dataset (default GoCJ 1000) |
| `--out` | folder hasil (default `scheduler/hasil`) |

### 3.7 `scheduler/dataset/GoCJ_Dataset_1000.csv`

Dataset yang sama dengan proyek CloudSim. 1000 baris, kolom pertama = panjang task (MI),
nilainya 15.000 sampai 900.000. Kolom lain tidak dipakai. Untuk ujicoba N task, diambil
N baris pertama, sehingga setiap run memakai task yang sama dengan urutan yang sama.

### 3.8 File hasil (`scheduler/hasil/` atau `hasil-azure/`)

| File | Isi |
|---|---|
| `HASIL_REALWORLD.md` | Tabel rata-rata dan detail tiap run |
| `ringkasan.csv` | Satu baris per run: makespan, cost, RU, DI, dan jumlah task tiap VM |
| `tasks_<N>_run<R>.csv` | Satu baris per task: dikirim ke VM mana, `start`, `finish` (detik sejak run dimulai), `exec_time` |

Kolom waktu di `HASIL_REALWORLD.md` memakai jam server Azure (UTC, 7 jam di belakang WIB).

---

## 4. Algoritma OLB di kode

Aturan OLB: **berikan task ke mesin yang paling awal siap, tanpa melihat kecepatan mesin.**

Inti di fungsi `run_olb`:

```python
ready_time = {n: 0.0 for n in names}   # kapan tiap VM terakhir kali jadi siap
idle = set(names)                      # VM yang sedang menganggur

for task in tasks:
    with cond:
        while not idle:                # semua VM sibuk? tunggu ada yang selesai
            cond.wait()
        # LANGKAH OLB: pilih VM menganggur dengan ready time paling awal
        vm = min(idle, key=lambda n: (ready_time[n], order[n]))
        idle.remove(vm)                # tandai VM itu sibuk
    threading.Thread(target=dispatch, args=(vm, task)).start()   # kirim task
```

Saat VM selesai, fungsi `dispatch` menjalankan:

```python
ready_time[vm] = done    # VM ini siap lagi pada detik ke-'done'
idle.add(vm)             # masukkan kembali ke daftar menganggur
cond.notify_all()        # bangunkan loop utama: ada VM kosong
```

- **Kenapa pakai thread?** Mengirim task berarti menunggu VM selesai. Tanpa thread,
  scheduler harus menunggu task 1 selesai sebelum mengirim task 2, sehingga hanya satu VM
  yang bekerja. Dengan thread, tiap pengiriman berjalan sendiri dan semua VM bisa sibuk bersamaan.
- **`cond` (Condition)** membuat loop utama bisa "tidur" saat semua VM sibuk dan
  "dibangunkan" saat ada VM yang selesai.
- **`order[n]`** pemecah seri. Di awal semua ready time = 0, jadi dipilih urutan daftar:
  host1-large dulu, lalu host1-medium, dan seterusnya.

### Perbedaan dengan versi CloudSim

| | CloudSim | Real world |
|---|---|---|
| Ready time | **Dihitung** di awal: `readyTime += length / MIPS` | **Diamati**: waktu VM benar-benar selesai |
| Kapan task dibagi | Semua task dibagi sebelum simulasi | Task dibagi satu per satu saat VM kosong |
| Hasil antar run | Identik (deterministik) | Sedikit berbeda (jaringan, fluktuasi CPU) |

Keduanya mengikuti aturan OLB yang sama. Bedanya, di dunia nyata durasi task tidak bisa
diketahui pasti sebelum dijalankan, jadi ready time harus diamati.

---

## 5. Metrik

Dihitung di fungsi `compute_metrics`. `busy` = total `exec_time` semua task di satu VM.

| Metrik | Rumus | Arti |
|---|---|---|
| Makespan | `max(finish)` | Detik dari task pertama dikirim sampai task terakhir selesai |
| Cost ($) | `(harga per jam host1 + host2) x makespan / 3600` | Biaya sewa infrastruktur selama eksekusi |
| Resource Utilization (%) | `sum(busy) / (jumlah VM x makespan) x 100` | Rata-rata seberapa sibuk VM |
| Degree of Imbalance | `(max(busy) - min(busy)) / rata-rata(busy)` | Seberapa timpang beban antar VM (0 = rata sempurna) |

Rumus RU dan DI sama dengan versi CloudSim.

---

## 6. Membaca hasil

- Makespan naik sebanding jumlah task.
- Hasil tiap run sedikit berbeda, tidak seperti CloudSim yang identik.
- Pada 100 task beban paling timpang (DI tertinggi): dengan task sedikit, satu task besar
  yang jatuh ke VM Small di akhir membuat VM lain menganggur menunggu. Ini kelemahan OLB
  yang tidak memperhitungkan kecepatan VM. Dengan task lebih banyak, beban lebih merata.
- Angka mentah tidak bisa dibandingkan langsung dengan CloudSim karena skala task dan
  kecepatan VM berbeda. Yang dibandingkan adalah polanya.

---

## 7. Keterbatasan

- VM diwujudkan sebagai container, bukan VM penuh. Container adalah virtualisasi ringan
  yang berbagi kernel dengan host.
- Panjang task diskala (`ITER_PER_MI = 20`), bukan jumlah instruksi sebenarnya.
- Host seri B bersifat *burstable*: performa CPU bisa naik turun, menambah variasi hasil.
- Scheduler ikut berjalan di host1, memakai sedikit CPU host1 (sebagian besar waktunya
  hanya menunggu balasan).
