# Panduan Pengujian Sekuensial 30 Run

Panduan ini untuk tahap revisi pertama: pengujian **sekuensial** 100 sampai 1.000 task,
masing-masing 3 run. Random sampling belum termasuk.

Urutannya wajib diikuti:

1. **Bagian A**: uji lokal di laptop Windows dengan Docker Desktop.
2. **Bagian B**: backup hasil lama di host1 (sebelum menjalankan apa pun di Azure).
3. **Bagian C**: memperbarui scheduler di host1.
4. **Bagian D**: daftar periksa sebelum benchmark.
5. **Bagian E**: benchmark Azure dalam 3 sesi.
6. **Bagian F**: mengambil hasil dan mematikan VM.

Setup Azure dari nol (membuat VM, jaringan, Docker) ada di [PENCERDASAN.md](PENCERDASAN.md).

---

## A. Uji lokal di laptop Windows

### A1. Persiapan (sekali saja)

1. Pasang **Docker Desktop** dan nyalakan. Tunggu sampai statusnya *Engine running*.
2. Pasang **Python 3** dari https://www.python.org/downloads/ dan centang
   **Add python.exe to PATH** saat instalasi. Versi 3.8 atau lebih baru cukup.
3. Buka PowerShell, cek:
   ```
   python --version
   docker --version
   ```
   Kalau `python` membuka Microsoft Store atau tidak dikenal, pakai `py` sebagai gantinya
   di semua perintah di bawah (misalnya `py olb_scheduler.py ...`).

Program hanya memakai library bawaan Python, jadi tidak perlu `pip install`.

### A2. Nyalakan 6 worker

Buka PowerShell **di folder `olb-realworld`** (di File Explorer: klik kanan area kosong,
pilih *Open in Terminal*).

```
docker compose up -d --build
docker compose ps
```

Harus tampil 6 container: `host1-large`, `host1-medium`, `host1-small`, `host2-large`,
`host2-medium`, `host2-small`, semuanya `running`. Di laptop keenamnya berjalan di satu mesin
dengan port 5001 sampai 5006.

### A3. Jalankan unit test

Masih di folder `olb-realworld`:

```
python -m unittest discover -s scheduler/tests -v
```

Butuh sekitar 1 menit. Baris terakhir harus:

```
Ran 49 tests in ...s

OK
```

Unit test tidak memakai Docker. Test menyalakan "mock worker" sendiri dan sengaja membuat
worker rusak (respons tanpa `exec_time`, HTTP 500, koneksi ditutup, timeout, task_id salah,
`ITER_PER_MI` salah, dan lainnya) untuk memastikan scheduler melapor gagal dan tidak macet.

### A4. Cek koneksi ke 6 worker

```
cd scheduler
python olb_scheduler.py --workers workers.local.json --check
```

Harus tampil 6 baris `OK` dengan `iter_per_mi=20`, diakhiri
`Semua 6 worker siap, ITER_PER_MI = 20.`

### A5. Uji kecil dalam dua sesi

Sesi 1 (run 1 saja):

```
python olb_scheduler.py --workers workers.local.json --name uji-laptop --sizes 100 200 300 --runs 3 --run-ids 1 --note "laptop sesi 1"
```

Sesi 2 dan 3 (melanjutkan sisanya):

```
python olb_scheduler.py --workers workers.local.json --name uji-laptop --resume --note "laptop sesi 2"
```

Cek progres dan cocokkan ulang ringkasan dengan data task:

```
python olb_scheduler.py --name uji-laptop --status
python olb_scheduler.py --name uji-laptop --verifikasi
```

Harus muncul `9 dari 9 run selesai` dan `VERIFIKASI OK: 9 run cocok dengan file task-nya.`

Buka folder hasil:

```
explorer hasil\sequential\uji-laptop
```

### A6. Simulasi worker gagal

1. Mulai eksperimen baru yang cukup lama:
   ```
   python olb_scheduler.py --workers workers.local.json --name uji-gagal --sizes 100 200 300 --runs 1
   ```
2. Saat tulisan `[RUN] 200 task` muncul, buka **PowerShell kedua** di folder `olb-realworld` dan
   matikan satu worker:
   ```
   docker compose stop host2-small
   ```
3. Di PowerShell pertama harus muncul `RUN GAGAL`, nama worker yang gagal, dan lokasi
   `log_kegagalan.txt`. Program berhenti sendiri (tidak menunggu selamanya).
   Run 100 task yang sudah selesai tetap ada di `ringkasan.csv`. Run 200 task **tidak** masuk ke
   ringkasan; task yang sempat selesai disimpan di folder `gagal/` untuk diperiksa.
4. Nyalakan lagi worker dan lanjutkan:
   ```
   docker compose start host2-small
   python olb_scheduler.py --workers workers.local.json --name uji-gagal --resume
   ```
   Run 200 dan 300 dijalankan ulang, run 100 tidak diulang.

### A7. Cek perlindungan data

Jalankan lagi perintah dengan nama yang sama **tanpa** `--resume`:

```
python olb_scheduler.py --workers workers.local.json --name uji-laptop --sizes 100 --runs 1
```

Program harus menolak dengan pesan `Eksperimen 'uji-laptop' sudah ada ... Pakai --resume`.
Tidak ada file yang berubah.

### A8. Matikan

Dari folder `olb-realworld`:

```
docker compose down
```

### Catatan uji lokal

- Angka di laptop **tidak mewakili Azure**. Di laptop keenam worker berbagi CPU yang sama
  (total batas 3,5 CPU), sehingga worker Small tidak terlihat 4 kali lebih lambat dari Large.
  Di Azure, tiap host hanya menjalankan 3 worker dengan total 1,75 dari 2 vCPU.
- Cost di laptop selalu 0 karena `workers.local.json` memakai tarif 0.
- Jangan menjalankan dua scheduler bersamaan ke worker yang sama. Hasilnya akan saling ganggu.

---

## B. Backup hasil lama di host1 (WAJIB, sebelum apa pun)

Hasil 26 Oktober (100/200/300 task x 3) kemungkinan masih ada di `~/olb-realworld/scheduler/hasil/`
di host1. Scheduler versi baru tidak menulis ke file-file itu, tetapi file tersebut tetap bisa
tertimpa kalau folder proyek disalin ulang dari laptop dengan `scp -r`. Jadi amankan dulu.

1. Nyalakan host1 di Azure Portal, lalu masuk:
   ```
   ssh azureuser@PUBLIC_IP_HOST1
   ```
2. Lihat isinya:
   ```
   cd ~/olb-realworld/scheduler
   ls -la hasil/
   ```
3. **Kalau ada file** (`ringkasan.csv`, `HASIL_REALWORLD.md`, `tasks_*.csv`), buat arsip dan
   sidik jarinya:
   ```
   mkdir -p ~/backup
   STAMP=$(date +%Y%m%d-%H%M)
   tar czf ~/backup/hasil-lama-$STAMP.tar.gz -C ~/olb-realworld/scheduler hasil
   (cd ~/olb-realworld/scheduler/hasil && sha256sum * > ~/backup/hasil-lama-$STAMP.sha256)
   tar tzf ~/backup/hasil-lama-$STAMP.tar.gz
   cat ~/olb-realworld/scheduler/hasil/ringkasan.csv
   exit
   ```
   Cocokkan isi `ringkasan.csv` dengan tabel di `hasil-azure/HASIL_REALWORLD.md`
   (misalnya makespan 100 task run 1 = 14.64). Kalau cocok, itu data asli 26 Oktober.
4. Unduh arsip ke laptop (dari PowerShell **laptop**, bukan dari dalam host1):
   ```
   scp "azureuser@PUBLIC_IP_HOST1:~/backup/hasil-lama-*" .
   ```
   Ekstrak dan simpan di repo sebagai `hasil-azure/historis-2026-10-26/`, lalu perbarui
   `hasil-azure/README.md`.
5. **Kalau folder kosong atau tidak ada**, jangan membuat ulang datanya. Tulis di
   `hasil-azure/README.md` bahwa CSV mentah tidak tersedia dan hanya ringkasan Markdown yang ada.

---

## C. Memperbarui scheduler di host1

Yang berubah hanya kode scheduler. **Worker tidak berubah**, jadi container di host1 dan host2
tidak perlu dibuild ulang atau dinyalakan ulang.

Jangan menyalin seluruh folder `olb-realworld` dengan `scp -r`, karena folder
`scheduler/hasil` dari laptop bisa ikut terkirim dan menimpa file di host1.
Salin hanya file yang berubah. Dari PowerShell laptop, di folder yang berisi `olb-realworld`:

```
scp olb-realworld/scheduler/olb_scheduler.py azureuser@PUBLIC_IP_HOST1:~/olb-realworld/scheduler/
scp -r olb-realworld/scheduler/tests azureuser@PUBLIC_IP_HOST1:~/olb-realworld/scheduler/
```

Lalu di host1:

```
ssh azureuser@PUBLIC_IP_HOST1
cd ~/olb-realworld/scheduler
python3 --version
python3 -m unittest discover -s tests
python3 olb_scheduler.py --workers workers.azure.json --check
```

Unit test di host1 opsional tetapi berguna (sekitar 1 menit, tidak memakai worker sungguhan).
Jalankan **sebelum** benchmark supaya tidak mengganggu pengukuran.

---

## D. Daftar periksa sebelum benchmark Azure

| # | Periksa | Cara |
|---|---|---|
| 1 | Uji lokal bagian A berhasil semua | 49 test OK, `--verifikasi` OK, simulasi gagal dan `--resume` berjalan |
| 2 | Backup hasil lama sudah diunduh ke laptop | Bagian B |
| 3 | Hanya `olb_scheduler.py` dan `tests/` yang disalin ke host1 | Bagian C |
| 4 | host1 dan host2 berstatus **Running** | Azure Portal, Virtual machines |
| 5 | 3 container hidup di tiap host | `sudo docker ps` di host1 dan host2 |
| 6 | Private IP host2 masih `172.16.0.5` | Azure Portal, host2, Networking. Kalau berubah, ubah `workers.azure.json` |
| 7 | Scheduler melihat 6 worker dengan `iter_per_mi=20` | `--check` |
| 8 | Port worker tidak terbuka ke internet | Lihat di bawah |
| 9 | CPU Credits Remaining kedua host cukup | Lihat bagian E1. Sebagai patokan kasar, minimal sekitar 30 per host sebelum satu sesi |
| 10 | Tidak ada proses berat lain di host1/host2 | Jangan menjalankan unit test atau eksperimen lain saat benchmark |
| 11 | `tmux` siap | Lihat bagian E2 |
| 12 | Budget Azure masih aman | Azure Portal, Cost Management |

**Cek port worker (NSG).** Worker memakai HTTP tanpa autentikasi, jadi port 5001 sampai 5003
hanya boleh diakses dari jaringan privat.

- Azure Portal, buka host1 lalu host2, menu **Networking** (*Network settings*), lihat
  **Inbound port rules**. Yang boleh terbuka ke internet hanya SSH (22). Tidak boleh ada aturan
  yang membuka 5001 sampai 5003 dengan source `Any` atau `Internet`. Aturan bawaan
  `AllowVnetInBound` sudah cukup supaya host1 bisa menghubungi host2.
- Uji dari PowerShell laptop (harus **gagal/timeout**):
  ```
  curl.exe -m 5 http://PUBLIC_IP_HOST2:5001/info
  ```
  Kalau malah keluar JSON, port worker terbuka ke internet. Hapus aturan inbound tersebut.

---

## E. Benchmark Azure: 3 sesi

Rencana: 10 ukuran x 3 run = 30 run, dibagi menjadi 3 sesi berdasarkan nomor run.

| Sesi | Isi | Perkiraan durasi |
|---|---|---|
| 1 | run 1 untuk 100, 200, ..., 1.000 task | sekitar 12 menit |
| 2 | run 2 untuk 100, 200, ..., 1.000 task | sekitar 12 menit |
| 3 | run 3 untuk 100, 200, ..., 1.000 task | sekitar 12 menit |

Perkiraan durasi berasal dari kecepatan run historis (sekitar 1 detik per 1 juta MI),
bukan dari pengukuran baru. Dengan pembagian ini, ukuran besar tidak menumpuk di akhir,
sehingga perubahan kondisi host tidak hanya mengenai run 1.000 task.

### E1. Memantau CPU credits

Lakukan sebelum dan sesudah setiap sesi, untuk host1 **dan** host2:

1. Azure Portal, buka VM, menu **Monitoring** lalu **Metrics**.
2. Metric: **CPU Credits Remaining**. Tambahkan juga **Percentage CPU** (*Add metric*).
3. Time range: *Last hour*, time granularity: *1 minute*.
4. Catat nilai terakhir. Data Azure Monitor bisa terlambat beberapa menit.
5. Ambil screenshot grafik setelah sesi sebagai bukti kondisi host.

Perkiraan kasar: saat benchmark, tiap host memakai sekitar 1,15 kredit per menit
(87,5% CPU dikurangi 30% performa dasar, dikali 2 vCPU). Satu sesi sekitar 12 menit berarti
sekitar 14 kredit per host. Saat idle, VM mengumpulkan kredit lagi. Kalau kredit salah satu
host di bawah sekitar 30, tunggu dulu sebelum sesi berikutnya. Semua angka ini perkiraan.

Kalau selama sesi kredit mendekati nol, catat waktunya dan tulis di laporan. Run yang terjadi
saat itu mungkin terpengaruh throttling.

### E2. Menjalankan di tmux

Sesi SSH bisa putus (Wi-Fi, laptop tidur). Kalau scheduler dijalankan langsung di SSH,
program ikut berhenti. Data run yang sudah selesai tetap aman dan bisa dilanjutkan dengan
`--resume`, tetapi lebih aman memakai `tmux`:

```
ssh azureuser@PUBLIC_IP_HOST1
tmux new -s olb
```

- Keluar sementara tanpa menghentikan program: tekan `Ctrl+B`, lepas, lalu tekan `D`.
- Masuk lagi: `tmux attach -t olb`.
- Kalau `tmux` belum ada: `sudo apt-get install -y tmux`.

### E3. Perintah per sesi

Di dalam tmux, di host1:

```
cd ~/olb-realworld/scheduler
python3 olb_scheduler.py --workers workers.azure.json --check
```

**Sesi 1:**

```
python3 olb_scheduler.py --workers workers.azure.json --name azure-seq-30run \
    --sizes 100 200 300 400 500 600 700 800 900 1000 --runs 3 --run-ids 1 \
    --note "sesi 1; credits sebelum: host1=..., host2=..."
```

**Sesi 2** (setelah cek CPU credits):

```
python3 olb_scheduler.py --workers workers.azure.json --name azure-seq-30run --resume --run-ids 2 \
    --note "sesi 2; credits sebelum: host1=..., host2=..."
```

**Sesi 3:**

```
python3 olb_scheduler.py --workers workers.azure.json --name azure-seq-30run --resume --run-ids 3 \
    --note "sesi 3; credits sebelum: host1=..., host2=..."
```

Nilai `--sizes` dan `--runs` hanya ditulis di sesi 1. Sesi berikutnya membaca rencana dari
`konfigurasi.json`. Kalau ditulis ulang dengan nilai berbeda, program menolak.

Setelah ketiga sesi:

```
python3 olb_scheduler.py --name azure-seq-30run --status
python3 olb_scheduler.py --name azure-seq-30run --verifikasi
```

Harus `30 dari 30 run selesai` dan `VERIFIKASI OK: 30 run cocok dengan file task-nya.`

### E4. Kalau ada run yang gagal

Program berhenti dengan pesan `RUN GAGAL` atau `[STOP] Worker tidak siap`. Langkahnya:

1. Baca `hasil/sequential/azure-seq-30run/gagal/log_kegagalan.txt`.
2. Cek container: `sudo docker ps` di host yang bermasalah. Kalau mati, jalankan lagi
   `bash ~/olb-realworld/worker/start_host.sh host2` (atau `host1`).
3. Jalankan `--check`.
4. Ulangi perintah sesi yang sama dengan `--resume`. Run yang sudah berhasil tidak diulang.

Run yang gagal tetap tercatat di folder `gagal/` dan di `log_sesi.csv`. Ini perlu disebut di
laporan. Jangan menghapus atau mengubah file hasil secara manual.

### E5. Catatan saat menjalankan

- Baris `<- xx% lebih lambat dari run pertama` di terminal adalah **indikasi** perubahan
  kecepatan worker, bukan bukti throttling. Cocokkan dengan grafik CPU Credits Remaining.
- Jangan menjalankan perintah lain yang berat di host1 atau host2 selama sesi.
- Jam di `ringkasan.csv` memakai zona waktu server (Azure: UTC, ditandai `+00:00`).

---

## F. Mengambil hasil dan mematikan VM

1. Keluar dari host1 (`exit`, juga keluar dari tmux kalau sudah selesai).
2. Dari PowerShell laptop:
   ```
   scp -r azureuser@PUBLIC_IP_HOST1:~/olb-realworld/scheduler/hasil/sequential/azure-seq-30run ./
   ```
3. Simpan di repo sebagai `hasil-azure/sequential/azure-seq-30run/` bersama screenshot
   CPU credits.
4. Azure Portal: **Stop** host1 dan host2 sampai statusnya **Stopped (deallocated)**.
   Mematikan lewat `sudo shutdown` dari dalam VM tidak menghentikan tagihan komputasi.

---

## G. Masalah yang mungkin muncul

| Pesan | Penyebab dan solusi |
|---|---|
| `TIDAK SIAP ... tidak bisa dihubungi` | Container mati atau IP/port salah. Cek `docker ps`, jalankan `start_host.sh`, cek Private IP host2 |
| `ITER_PER_MI=..., diharapkan 20` | Ada container yang dinyalakan dengan nilai lain. Jalankan ulang `start_host.sh` tanpa variabel `ITER_PER_MI` |
| `mengaku bernama ...` | Port di `workers.azure.json` tertukar atau container salah nama |
| `Eksperimen ... sudah ada` | Pakai `--resume` untuk melanjutkan, atau `--name` lain untuk eksperimen baru |
| `konfigurasi berbeda dari eksperimen awal` | `--sizes`, `--runs`, dataset, atau daftar worker berubah. Pakai nilai yang sama atau nama eksperimen baru |
| `[STOP] Worker tidak siap sebelum ...` setelah timeout | Worker mungkin masih mengerjakan task lama. Tunggu 1 sampai 2 menit, lalu `--resume` |
| `python` tidak dikenal di Windows | Pakai `py` |
