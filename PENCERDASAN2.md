# PENCERDASAN 2: Cara Kerja Kode

Penjelasan detail tiap file dan konsep di balik proyek ini. Cara setup ada di
[PENCERDASAN.md](PENCERDASAN.md), cara menjalankan pengujian ada di
[PANDUAN_PENGUJIAN.md](PANDUAN_PENGUJIAN.md).

Istilah: **host** = Azure VM (`host1`, `host2`); **worker** = Docker container (`host1-large`, dst.),
representasi logis VM CloudSim; **scheduler** = proses Python di host1.

---

## 1. Alur kerja dari awal sampai akhir

1. **Scheduler mengecek worker.** Semua worker dihubungi lewat `GET /info`. Scheduler memastikan
   nama dan host tiap worker sesuai file konfigurasi, dan semua memakai `ITER_PER_MI = 20`.
2. **Scheduler membaca dataset.** Ambil N baris pertama GoCJ; tiap baris = panjang satu task
   dalam MI. Contoh baris pertama: `83000`.
3. **Scheduler memeriksa worker yang idle** dan memilih satu dengan aturan OLB (bagian 4).
   Kalau semua sibuk, scheduler menunggu.
4. **Scheduler mengirim task** lewat jaringan: `POST http://<ip>:<port>/task` dengan isi
   `{"task_id": 0, "length": 83000}`.
5. **Worker mengerjakan task**: memutar loop sebanyak `length x ITER_PER_MI` kali.
   Karena batas CPU tiap worker berbeda, worker Large selesai lebih cepat daripada Small.
6. **Worker membalas** dengan waktu eksekusi, misalnya `{"exec_time": 0.29, ...}`.
7. **Scheduler memeriksa respons** (lengkap, task_id dan length cocok, exec_time valid), mencatat
   waktu kirim dan waktu respons diterima, lalu menandai worker itu siap lagi.
8. Langkah 3 sampai 7 diulang sampai semua task selesai.
9. **Scheduler memvalidasi run**, menghitung empat metrik, lalu **langsung menyimpan** hasil run
   tersebut sebelum lanjut ke run berikutnya.

Pemetaan dari CloudSim (analogi, tidak identik):

| Konsep | Di CloudSim | Di proyek ini |
|---|---|---|
| Datacenter | `DatacenterSimple` | Tidak ada padanan fisik pasti; resource group Azure `soka8-dc` di region Indonesia Central |
| Host | `HostSimple` | Azure VM B2als_v2 (2 vCPU, 4 GiB) |
| VM | `VmSimple` dengan MIPS | Worker: Docker container dengan batas CPU |
| Cloudlet | `CloudletSimple` dengan length (MI) | Task berisi length (MI) dikirim lewat jaringan |
| Eksekusi task | Dimodelkan: `length / MIPS` | Dijalankan: CPU benar-benar bekerja |
| Scheduler | `applyOlbScheduling()` | `run_olb()` di `scheduler/olb_scheduler.py` |

---

## 2. Konsep yang perlu dipahami

### 2.1 Dari MI ke kerja nyata (`ITER_PER_MI`)

MI (Million Instructions) adalah ukuran "berat" task. Di CloudSim, durasi task cukup
dimodelkan: `length / MIPS`. Di dunia nyata kita tidak bisa menyuruh CPU menjalankan
"tepat 83.000 juta instruksi", dan GoCJ tidak menyediakan kode asli tiap pekerjaan. Jadi
panjang task dipakai sebagai **bobot** untuk membentuk beban CPU sintetis:

```python
# worker/worker.py
def burn_cpu(iterations):
    x = 0
    for i in range(iterations):
        x = (x + i * i) % 1_000_003   # hitungan kecil, tujuannya hanya membuat CPU sibuk
    return x

burn_cpu(length * ITER_PER_MI)        # ITER_PER_MI = 20
```

Contoh: task 83.000 MI dikerjakan dengan 83.000 x 20 = **1.660.000 iterasi**.
Task 166.000 MI jadi 3.320.000 iterasi, dua kali lebih banyak. Jadi:

- **Rasio beban antartask tetap sama** dengan dataset.
- Satu iterasi Python **bukan** satu instruksi CPU, sehingga jumlah instruksi mesin tidak sama
  dengan MI di CloudSim. Waktu eksekusi CloudSim dan Azure tidak dibandingkan langsung.
- Angka 20 adalah parameter konversi yang sudah dipakai sejak pengujian pertama, bukan ketentuan
  dataset GoCJ, dan belum dikalibrasi terhadap MIPS tertentu.
- Panjang MI asli tetap disimpan di dataset dan di log task (`length_mi`), bersama jumlah
  iterasinya (`iterations`).

### 2.2 Batas CPU = kecepatan worker

Di CloudSim kecepatan VM diatur lewat MIPS. Di sini lewat batas CPU Docker:

```bash
docker run --cpus 1.0  ...   # Large : boleh pakai 100% dari 1 core
docker run --cpus 0.5  ...   # Medium: maksimal 50% dari 1 core
docker run --cpus 0.25 ...   # Small : maksimal 25% dari 1 core
```

Di Azure, tiap host 2 vCPU menjalankan 3 worker dengan total batas 1,75 CPU, jadi loop yang
sama di worker Small kira-kira butuh waktu 4 kali lebih lama daripada di Large. Di laptop,
keenam worker berbagi CPU yang sama sehingga perbandingan ini tidak selalu terlihat.

### 2.3 Kenapa ada port

Tiap worker adalah **program server kecil** yang menunggu kiriman task. Supaya scheduler bisa
mengirim ke worker tertentu, worker itu butuh **alamat**: IP + port.

Analogi: **IP = alamat gedung (host), port = nomor kamar (worker)**.

- `172.16.0.5:5001` = gedung host2, kamar 5001 = worker host2-large
- `172.16.0.5:5003` = gedung host2, kamar 5003 = worker host2-small

Di dalam container, worker selalu mendengarkan di port 5000. Docker menyambungkan port
host ke port container:

```bash
-p 5001:5000    # pintu 5001 di host diteruskan ke pintu 5000 di dalam container Large
-p 5002:5000    # ke container Medium
-p 5003:5000    # ke container Small
```

Tiga worker dalam satu host tidak bisa memakai nomor pintu yang sama, makanya 5001, 5002, 5003.
host2 boleh memakai nomor yang sama karena IP-nya berbeda. Port ini tidak boleh dibuka ke
internet (lihat PENCERDASAN.md bagian 8).

### 2.4 Cara scheduler dan worker berkomunikasi

Scheduler dan worker berbicara lewat HTTP. Worker hanya punya dua alamat:

| Alamat | Fungsi |
|---|---|
| `GET /info` | "Kamu siapa?" Mengembalikan nama, host, tipe, batas CPU, dan `iter_per_mi`. Dipakai `--check` dan dicek ulang sebelum tiap run |
| `POST /task` | "Kerjakan task ini." Worker menjalankan loop lalu membalas `exec_time` |

Bisa dicoba saat uji di laptop: buka `http://localhost:5001/info` di browser.

### 2.5 Satu worker hanya mengerjakan satu task sekaligus

Worker memakai `HTTPServer` bawaan Python yang **single-threaded**: selama satu task
dikerjakan, permintaan lain menunggu. Scheduler juga tidak pernah mengirim task kedua ke worker
yang belum membalas. Validasi otomatis memeriksa hal ini dari log: untuk setiap worker, task
berikutnya tidak boleh dimulai sebelum task sebelumnya selesai.

### 2.6 Docker: image dan container

- **Image** = cetakan, dibuat dari `worker/Dockerfile`.
- **Container** = hasil cetakan yang sedang berjalan. Satu image dipakai untuk 3 container
  dengan pengaturan berbeda (batas CPU, port, nama).

---

## 3. Penjelasan tiap file

### 3.1 `worker/worker.py` (program worker)

Program yang berjalan di dalam tiap container. **Tidak diubah** pada revisi tahap 1.

| Bagian | Fungsi |
|---|---|
| `VM_NAME`, `HOST_NAME`, `VM_TYPE`, `VM_CPUS` | Identitas worker, dibaca dari variabel lingkungan yang diisi saat container dinyalakan (nama variabel lama dipertahankan) |
| `ITER_PER_MI` | Faktor konversi MI ke iterasi (default 20) |
| `PORT` | Port yang didengarkan di dalam container (5000) |
| `burn_cpu()` | Loop yang membuat CPU sibuk, mewakili eksekusi task |
| `do_GET` | Menjawab `/info` dengan identitas worker |
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

### 3.3 `worker/start_host.sh` (menyalakan 3 worker di satu host)

Dipanggil dengan `bash start_host.sh host1`. Langkahnya:

1. `docker build -t olb-worker .` membuat image dari Dockerfile.
2. Menjalankan 3 container lewat fungsi `run_vm TIPE CPU PORT`:

| Worker | `--cpus` | Port host |
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
| `--cpus 1.0` | batas CPU (kecepatan worker) |
| `-p 5001:5000` | sambungan port (alamat worker) |
| `-e VM_NAME=... -e VM_TYPE=...` | identitas worker, dibaca oleh `worker.py` |
| `-e ITER_PER_MI=20` | faktor konversi task |

Sebelum menyalakan, container lama dengan nama sama dihapus dulu (`docker rm -f`), jadi
script aman dijalankan berulang kali.

### 3.4 `docker-compose.yml` (khusus uji di laptop)

Menyalakan 6 container sekaligus di satu laptop untuk mensimulasikan 2 host x 3 worker.
Isinya sama dengan `start_host.sh`, bedanya port dibuat 5001 sampai 5006 karena semua
berada di satu mesin:

| Container | `cpus` | Port laptop |
|---|---|---|
| host1-large / medium / small | 1.0 / 0.5 / 0.25 | 5001 / 5002 / 5003 |
| host2-large / medium / small | 1.0 / 0.5 / 0.25 | 5004 / 5005 / 5006 |

### 3.5 `scheduler/workers.local.json` dan `scheduler/workers.azure.json` (daftar worker)

File ini memberi tahu scheduler **worker mana saja yang ada dan di mana alamatnya**.

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
| `host_price_per_hour` | Asumsi tarif tiap host per jam (USD) untuk Estimated Compute Cost. Angka 0.0428 diambil dari estimasi harga Azure Portal saat VM B2als_v2 dibuat di Indonesia Central |
| `workers` | Daftar 6 worker. Urutannya juga dipakai sebagai pemecah seri OLB |
| `name` | Nama worker. Harus sama dengan `VM_NAME` di container (dicek lewat `/info`) |
| `host` | Host tempat worker berada. Harus sama dengan `HOST_NAME` di container |
| `url` | Alamat worker = IP + port |

Worker host1 memakai `localhost` karena scheduler berjalan **di host1 itu sendiri**. Worker host2
dihubungi lewat Private IP-nya (`172.16.0.5`) melalui jaringan privat Azure.
`workers.local.json` memakai `localhost` port 5001 sampai 5006 dengan tarif 0.

### 3.6 `scheduler/olb_scheduler.py` (scheduler OLB)

| Fungsi | Tugas |
|---|---|
| `http_get`, `http_post` | Mengirim pesan ke worker (dengan batas waktu) |
| `load_gocj(path, n)` | Membaca n task pertama. Tiap task punya `task_id` (mulai 0), `length` (MI), dan `dataset_row` (nomor baris asli di CSV, mulai 1) |
| `dataset_info(path)` | Nama file, jumlah baris, dan sha256 dataset, untuk memastikan dataset tidak berubah antar-sesi |
| `check_workers(...)` | Cek `/info` semua worker: bisa dihubungi, nama/host cocok, `ITER_PER_MI` sesuai |
| `parse_task_response(...)` | Memastikan respons worker lengkap dan cocok dengan task yang dikirim |
| `run_olb(tasks, workers)` | Inti algoritma OLB (bagian 4) dan penanganan kegagalan |
| `validate_run(...)`, `validate_metrics(...)` | Validasi otomatis hasil run (bagian 6) |
| `compute_metrics(...)` | Menghitung makespan, cost, RU, DI (bagian 5). Rumus tidak diubah |
| `worker_perf(...)` | Data pendukung: waktu sibuk dan detik per juta iterasi tiap worker |
| `Experiment` | Mengelola folder hasil, pencatatan kegagalan, dan resume |
| `build_report(...)` | Menyusun ulang `HASIL_REALWORLD.md` dari `ringkasan.csv` setiap run selesai |
| `verify_experiment(...)` | Menghitung ulang metrik dari file task dan mencocokkannya dengan ringkasan |
| `main()` | Membaca opsi, cek worker, menjalankan rencana pengujian, menyimpan hasil |

Opsi perintah:

| Opsi | Arti |
|---|---|
| `--workers` | file daftar worker (`workers.local.json` atau `workers.azure.json`) |
| `--check` | hanya cek semua worker bisa dihubungi dan `ITER_PER_MI` sesuai, lalu berhenti |
| `--name` | nama eksperimen; hasil disimpan di `hasil/sequential/<name>/` (default: waktu sekarang) |
| `--sizes` | jumlah task yang diuji, misal `100 200 300` (hanya saat eksperimen baru dibuat) |
| `--runs` | berapa kali tiap ukuran diulang (hanya saat eksperimen baru dibuat) |
| `--run-ids` | hanya jalankan nomor run tertentu pada sesi ini, misal `--run-ids 1` |
| `--order` | `run` (default): run 1 semua ukuran, lalu run 2, dst.; `size`: 100x3, 200x3, dst. |
| `--resume` | lanjutkan eksperimen yang sudah ada; run yang sudah berhasil dilewati |
| `--note` | catatan sesi, misalnya sisa CPU credits; disimpan di `log_sesi.csv` |
| `--status` | tampilkan run mana yang sudah dan belum selesai |
| `--verifikasi` | hitung ulang metrik dari file task dan cocokkan dengan `ringkasan.csv` |
| `--expect-iter` | `ITER_PER_MI` yang wajib dipakai semua worker (default 20) |
| `--task-timeout` | batas waktu satu task dalam detik (default 600) |
| `--dataset` | file dataset (default GoCJ 1000) |
| `--out` | folder induk hasil (default `scheduler/hasil`) |

### 3.7 `scheduler/dataset/GoCJ_Dataset_1000.csv`

Dataset yang sama dengan proyek CloudSim. 1000 baris, kolom pertama = panjang task (MI),
nilainya 15.000 sampai 900.000. Kolom lain tidak dipakai. Untuk N task diambil N baris pertama,
sehingga setiap run memakai task yang sama dengan urutan yang sama.

### 3.8 File hasil (`scheduler/hasil/sequential/<nama-eksperimen>/`)

| File | Isi |
|---|---|
| `konfigurasi.json` | Mode sampling, dataset (nama, jumlah baris, sha256), `ITER_PER_MI`, daftar worker beserta tipe dan batas CPU dari `/info`, tarif host, ukuran dan jumlah run, urutan, host scheduler, versi Python |
| `ringkasan.csv` | Satu baris per run yang lolos validasi, ditambahkan begitu run selesai |
| `HASIL_REALWORLD.md` | Laporan otomatis: progres, rata-rata ± simpangan baku per ukuran, detail run, jumlah task per worker, kecepatan worker, cara membaca metrik |
| `kinerja_worker.csv` | Per run per worker: jumlah task, total MI, waktu sibuk, detik per juta iterasi |
| `log_sesi.csv` | Per sesi: waktu mulai/selesai, run yang direncanakan, jumlah run selesai, status, catatan |
| `tasks/tasks_<N>_run<R>.csv` | Satu baris per task |
| `gagal/log_kegagalan.txt` | Alasan setiap run yang gagal |
| `gagal/tasks_<N>_run<R>_gagal_<waktu>.csv` | Task yang sempat selesai pada run yang gagal (untuk diagnosis, tidak dipakai di ringkasan) |

Kolom `ringkasan.csv`. Enam kolom pertama dan kolom `task_<worker>` sama dengan versi lama:

| Kolom | Arti |
|---|---|
| `tasks`, `run` | Jumlah task dan nomor run |
| `makespan` | Detik |
| `cost` | Estimated Compute Cost (USD). Nama kolom lama dipertahankan |
| `ru`, `di` | Resource Utilization (%) dan Degree of Imbalance |
| `task_<worker>` | Jumlah task yang dikerjakan tiap worker |
| `sampling` | `sequential` |
| `dataset`, `iter_per_mi`, `total_mi` | Sumber dataset, faktor konversi, total MI pada run itu |
| `urutan`, `sesi` | Urutan eksekusi global dan nomor sesi |
| `waktu_mulai`, `waktu_selesai` | Waktu server dengan zona waktu (Azure memakai UTC) |
| `status` | `ok` (hanya run yang lolos validasi yang ditulis) |

Kolom `tasks_<N>_run<R>.csv`:

| Kolom | Arti |
|---|---|
| `task_id` | Nomor urut task mulai 0 |
| `length_mi` | Panjang task asli dari GoCJ |
| `vm`, `host`, `type` | Worker penerima, host-nya, dan tipenya (nama kolom `vm` dipertahankan dari versi lama) |
| `start`, `finish` | Detik sejak run dimulai, dicatat scheduler: saat task dikirim dan saat respons diterima |
| `exec_time` | Waktu eksekusi yang diukur di dalam worker |
| `dataset_row` | Nomor baris asli di CSV GoCJ (mulai 1) |
| `iterations` | `length_mi x ITER_PER_MI` |
| `status` | `ok` |

Selisih `(finish - start) - exec_time` adalah overhead komunikasi dan antrean untuk task itu.

---

## 4. Algoritma OLB di kode

Aturan OLB: **berikan task ke worker yang sedang idle; kalau beberapa idle, pilih yang paling
awal siap. Kecepatan worker dan ukuran task tidak diperhitungkan.**

Inti di fungsi `run_olb` (tidak berubah prinsipnya dari versi pertama):

```python
ready_time = {n: 0.0 for n in names}   # kapan tiap worker terakhir kali jadi siap
idle = set(names)                      # worker yang sedang menganggur

for task in tasks:
    with cond:
        while not idle and not errors: # semua sibuk? tunggu ada yang selesai (atau ada yang gagal)
            cond.wait()
        if errors:
            break                      # berhenti mengirim task baru kalau ada kegagalan
        # LANGKAH OLB: pilih worker idle dengan ready time paling awal
        vm = min(idle, key=lambda n: (ready_time[n], order[n]))
        idle.remove(vm)                # tandai worker itu sibuk
    threading.Thread(target=dispatch, args=(vm, task)).start()   # kirim task
```

Saat worker membalas, fungsi `dispatch` memeriksa respons lalu menjalankan:

```python
ready_time[vm] = done    # worker ini siap lagi pada detik ke-'done'
idle.add(vm)             # masukkan kembali ke daftar menganggur
cond.notify_all()        # bangunkan loop utama: ada worker kosong
```

- **Kenapa pakai thread?** Mengirim task berarti menunggu worker selesai. Tanpa thread,
  hanya satu worker yang bekerja pada satu waktu.
- **`cond` (Condition)** membuat loop utama bisa "tidur" saat semua worker sibuk dan
  "dibangunkan" saat ada worker yang selesai atau gagal.
- **`order[n]`** pemecah seri. Di awal semua ready time = 0, jadi dipilih urutan daftar:
  host1-large dulu, lalu host1-medium, dan seterusnya.
- Setelah awal run, biasanya hanya satu worker yang idle pada satu saat (yang baru selesai),
  sehingga task berikutnya langsung diberikan ke worker itu.

### Penanganan kegagalan

Kalau pengiriman gagal (koneksi ditolak/putus, timeout, HTTP error, respons bukan JSON,
kolom hilang, `task_id`/`length`/nama worker tidak cocok, atau `exec_time` tidak valid):

1. Kegagalan dicatat, dan worker yang gagal **tidak** dikembalikan ke daftar idle.
2. Loop utama dibangunkan dan berhenti mengirim task baru, jadi tidak ada yang menunggu selamanya.
3. Task yang sedang berjalan di worker lain ditunggu sampai selesai (dibatasi `--task-timeout`).
4. Run dinyatakan gagal: tidak masuk `ringkasan.csv`, task yang sempat selesai disimpan di `gagal/`.
5. Program berhenti dengan pesan jelas. Run sebelumnya sudah tersimpan dan bisa dilanjutkan
   dengan `--resume`.

### Perbedaan dengan versi CloudSim

| | CloudSim | Real world |
|---|---|---|
| Ready time | **Dimodelkan** di awal: `readyTime += length / MIPS` | **Diamati**: saat respons worker diterima scheduler |
| Kapan task dibagi | Semua task dibagi sebelum simulasi berjalan | Task dibagi satu per satu saat ada worker idle |
| Durasi task | Hasil model | Waktu nyata, termasuk variasi CPU |
| Overhead jaringan | Tidak ada | Ikut masuk ke makespan |
| Hasil antar run | Identik (deterministik) | Berbeda walaupun input identik |

Aturan pemilihannya sama (worker yang paling awal siap), tetapi mekanismenya tidak identik,
sehingga hasil kedua implementasi tidak diharapkan sama.

---

## 5. Metrik

Dihitung di fungsi `compute_metrics`. `busy` = total `exec_time` semua task di satu worker.
Rumus tidak diubah dari versi pertama.

| Metrik | Rumus | Arti |
|---|---|---|
| Makespan (s) | `max(finish)` | Detik dari awal penjadwalan batch sampai respons task terakhir diterima scheduler |
| Estimated Compute Cost (USD) | `(tarif host1 + tarif host2) x makespan / 3600` | Estimasi biaya komputasi dua host selama makespan, bukan tagihan Azure |
| Resource Utilization (%) | `sum(busy) / (jumlah worker x makespan) x 100` | Utilisasi waktu kerja worker, bukan `Percentage CPU` Azure, tidak dibobot kapasitas |
| Degree of Imbalance | `(max(busy) - min(busy)) / rata-rata(busy)` | Ketimpangan waktu sibuk antarworker (0 = rata) |

Karena `busy` diukur di dalam worker, RU tidak menghitung waktu jaringan sebagai waktu kerja.
Validasi memastikan RU tidak pernah melebihi 100%.

---

## 6. Validasi otomatis

Setiap run diperiksa sebelum disimpan. Kalau ada satu saja yang tidak lolos, run dianggap gagal.

| Pemeriksaan | Fungsi |
|---|---|
| Jumlah task berhasil = jumlah yang diminta | `validate_run` |
| Tidak ada task duplikat, hilang, atau tidak dikenal | `validate_run` |
| Panjang task dan nomor baris dataset sesuai yang dikirim | `validate_run` |
| Setiap task punya worker tujuan yang terdaftar, dengan host yang benar | `validate_run` |
| Waktu valid: `start >= 0`, `finish >= start`, `exec_time >= 0`, tidak NaN | `validate_run` |
| `exec_time` tidak lebih besar dari waktu tunggu scheduler | `validate_run` |
| Satu worker tidak mengerjakan dua task bersamaan | `validate_run` |
| Makespan positif dan sama dengan finish task terakhir | `validate_run` |
| Jumlah task per worker dan total waktu sibuk konsisten dengan data detail | `validate_metrics` |
| Empat metrik bernilai valid, RU tidak lebih dari 100% | `validate_metrics` |

Selain itu, `--verifikasi` menghitung ulang metrik dari semua file `tasks/*.csv` dan
mencocokkannya dengan `ringkasan.csv`. Ini berguna saat demo untuk menunjukkan bahwa
ringkasan benar-benar berasal dari data detail.

---

## 7. Penyimpanan dan resume

- Setiap eksperimen punya folder `hasil/sequential/<nama>/`. Kalau folder itu sudah ada,
  program menolak berjalan kecuali diberi `--resume`.
- File task dibuat dengan mode "harus baru" (`open(..., "x")`), jadi tidak mungkin menimpa file lama.
- Urutan simpan per run: file task, lalu baris `ringkasan.csv`, lalu `kinerja_worker.csv`,
  lalu `HASIL_REALWORLD.md` disusun ulang. Kalau program berhenti di tengah, run yang sudah
  ada di ringkasan tetap aman.
- `--resume` membaca `ringkasan.csv` untuk tahu run mana yang sudah selesai dan hanya menjalankan
  sisanya. Rencana (ukuran, jumlah run, urutan), dataset (sha256), `ITER_PER_MI`, daftar worker,
  dan tarif harus sama dengan eksperimen awal.
- Kalau ditemukan file task tanpa baris ringkasan (program mati tepat saat menyimpan), file itu
  dipindah ke `gagal/` dengan akhiran `_tidak_lengkap_`, lalu run diulang.
- File di `scheduler/hasil/` di luar folder `sequential/` (hasil scheduler versi lama) tidak
  pernah disentuh.

---

## 8. Membaca hasil

- Makespan naik seiring jumlah task (lebih tepatnya seiring total MI).
- Hasil tiap run berbeda walaupun input identik, tidak seperti CloudSim.
- Pada jumlah task kecil (100), DI cenderung lebih tinggi: satu task besar yang jatuh ke worker
  Small di akhir membuat worker lain menganggur menunggu. Ini kelemahan OLB yang tidak
  memperhitungkan kecepatan worker. Dengan task lebih banyak, efek ini relatif lebih kecil.
- Karena worker hampir tidak pernah menganggur selama masih ada task, DI berbasis waktu sibuk
  cenderung rendah. Ketimpangan lebih terlihat di **jumlah task per worker**.
- Kolom detik per juta iterasi di `kinerja_worker.csv` membantu melihat apakah kecepatan worker
  berubah antar-run. Perubahan besar adalah indikasi, bukan bukti throttling; cocokkan dengan
  Azure Monitor.
- Angka mentah tidak dibandingkan langsung dengan CloudSim. Yang dibandingkan adalah polanya.

---

## 9. Keterbatasan

- Worker diwujudkan sebagai container, bukan VM penuh. Container berbagi kernel dengan host.
- Panjang task dikonversi (`ITER_PER_MI = 20`), bukan jumlah instruksi sebenarnya, dan faktor ini
  belum dikalibrasi.
- Host seri B bersifat burstable: performa CPU dapat turun kalau CPU credits habis.
- Scheduler ikut berjalan di host1 dan memakai sebagian resource host1 (sebagian besar waktunya
  hanya menunggu balasan).
- Worker host1 dihubungi lewat `localhost`, host2 lewat jaringan privat; overhead komunikasinya
  sedikit berbeda.
- RU berbasis waktu kerja worker, bukan pengukuran CPU. Estimated Compute Cost bukan tagihan nyata.
