# Revisi Tahap 1: Scheduler, Penyimpanan Hasil, Validasi, Dokumentasi

Tanggal: 31 Oktober 2026. Cakupan: pengujian **sekuensial** 100 sampai 1.000 task x 3 run.
Random sampling **tidak** diimplementasikan pada tahap ini.

## 1. Yang tidak diubah

| Komponen | Status |
|---|---|
| Algoritma OLB (`min(idle, key=(ready_time, order))`) | Sama |
| Rumus Makespan, Cost, RU, DI (`compute_metrics`) | Sama |
| `worker/worker.py`, `worker/Dockerfile`, `worker/start_host.sh` | Tidak diubah |
| `docker-compose.yml`, `workers.azure.json`, `workers.local.json` | Tidak diubah |
| Dataset `GoCJ_Dataset_1000.csv`, `ITER_PER_MI = 20` | Tidak diubah |
| Arsitektur: 2 Azure VM, 6 worker, scheduler di host1 | Tidak diubah |
| `hasil-azure/HASIL_REALWORLD.md` (data historis) | Tidak diubah |

Komentar di dalam `worker.py`, `start_host.sh`, dan `docker-compose.yml` masih menyebut container
sebagai "VM". Ini sengaja dibiarkan supaya file worker tetap identik dengan yang sudah berjalan di
Azure. Istilah yang benar dipakai di semua dokumen.

**Karena worker tidak berubah, container di host1 dan host2 tidak perlu dibuild ulang atau
dinyalakan ulang.** Cukup salin `scheduler/olb_scheduler.py` (dan `scheduler/tests/`) ke host1.
Isi request ke worker juga sama persis seperti sebelumnya (`{"task_id", "length"}`).

## 2. Daftar file yang diubah atau ditambah

| File | Status | Alasan |
|---|---|---|
| `scheduler/olb_scheduler.py` | Diubah | Penyimpanan per run, folder terpisah tanpa menimpa, resume, penanganan error, validasi respons dan hasil, validasi `ITER_PER_MI`, pencatatan konfigurasi dan sesi, laporan dengan simpangan baku, `--status`, `--verifikasi` |
| `scheduler/tests/test_olb_scheduler.py` | Baru | 49 unit test dan uji error dengan mock worker |
| `README.md` | Ditulis ulang | Terminologi, arsitektur, OLB, `ITER_PER_MI`, metode, empat metrik, CPU credits, keterbatasan |
| `PENCERDASAN.md` | Diperbarui | Terminologi host/worker, uji lokal, peringatan `scp -r`, metrik, keamanan jaringan. Langkah setup Azure (bagian B) dipertahankan |
| `PENCERDASAN2.md` | Diperbarui | Penjelasan kode baru, penanganan kegagalan, validasi, format file hasil, perbedaan dengan CloudSim |
| `PANDUAN_PENGUJIAN.md` | Baru | Uji lokal Windows, backup hasil lama, update scheduler, daftar periksa, 3 sesi Azure, CPU credits, tmux |
| `REVISI_TAHAP1.md` | Baru | Dokumen ini |
| `hasil-azure/README.md` | Baru | Label data historis dan status CSV mentahnya |
| `analisis/buat_grafik.py`, `grafik/` | Baru | Grafik hasil Azure dan perbandingan dengan CloudSim |
| `.gitignore` | Diubah | Menambah `*.tmp` (file sementara saat laporan ditulis) |

## 3. Masalah yang diperbaiki di scheduler

| Masalah di versi lama | Perbaikan |
|---|---|
| `ringkasan.csv` dan `HASIL_REALWORLD.md` baru ditulis setelah **semua** run selesai. Kalau berhenti di tengah, ringkasan hilang | Setiap run langsung disimpan: file task, baris ringkasan, kinerja worker, lalu laporan disusun ulang |
| Semua run menulis ke `scheduler/hasil/` dengan nama tetap, sehingga menimpa hasil sebelumnya | Hasil ke `hasil/sequential/<nama>/`. Folder yang sudah ada ditolak tanpa `--resume`. File task dibuat dengan mode "harus baru" |
| Tidak bisa melanjutkan pengujian yang terhenti | `--resume` melewati run yang sudah berhasil. `--run-ids` untuk membagi sesi |
| `resp["exec_time"]` dibaca di luar `try`. Kalau respons tidak lengkap, thread mati, worker tidak pernah kembali idle, dan scheduler bisa menunggu selamanya | Seluruh pemrosesan respons ada di dalam `try`. Loop utama juga bangun kalau ada kegagalan (`while not idle and not errors`) |
| Respons worker tidak diperiksa | `task_id`, `length`, nama worker, dan `exec_time` (angka, tidak negatif, bukan NaN) dicek |
| Worker yang gagal dikembalikan ke daftar idle | Worker gagal tidak dipakai lagi; run dihentikan dan dinyatakan gagal |
| Kegagalan menghentikan program dengan `SystemExit` tanpa jejak | Kegagalan dicatat di `gagal/log_kegagalan.txt`, task yang sempat selesai disimpan terpisah, sesi dicatat di `log_sesi.csv` |
| Timeout per task 3.600 detik | Default 600 detik (`--task-timeout`). Task terbesar diperkirakan belasan detik di worker Small |
| `iter_per_mi` dari `/info` diabaikan | Keenam worker wajib `ITER_PER_MI = 20`; nama dan host worker juga dicek. Dicek lagi sebelum tiap run |
| Tidak ada validasi hasil | Validasi otomatis sebelum disimpan (lihat PENCERDASAN2.md bagian 6) dan `--verifikasi` untuk mencocokkan ulang |
| Log task tidak mencatat baris dataset dan jumlah iterasi | Kolom baru `dataset_row`, `iterations`, `status`. Kolom lama dipertahankan |

Perubahan perilaku yang perlu diketahui:

- **Urutan default** sekarang `run` (run 1 untuk semua ukuran, lalu run 2, dst.). Urutan lama
  tersedia dengan `--order size`.
- Perintah lama (`--sizes 100 200 300 --runs 3`) masih berjalan, tetapi hasilnya masuk ke folder
  eksperimen baru bernama waktu sekarang.
- `--check` sekarang gagal kalau nama worker, host, atau `ITER_PER_MI` tidak sesuai. Di Azure,
  nama dari `start_host.sh` (`host1-large`, dst.) sudah sesuai dengan `workers.azure.json`.

## 4. Pengujian yang benar-benar dijalankan

Lingkungan: workspace Linux milik Claude (2 CPU), Python 3.13, Docker 29.9.2. **Bukan laptop
Windows dan bukan Azure.** Base image `python:3.12-slim` tidak bisa diunduh dari workspace ini
(Docker Hub diblokir), jadi dibuat image pengganti lokal berisi Python 3.13 dengan tag yang sama.
`worker.py`, `Dockerfile`, dan `docker-compose.yml` dipakai tanpa perubahan.

| Pengujian | Hasil |
|---|---|
| Unit test (`python -m unittest discover -s scheduler/tests`) | **49 test OK**, sekitar 60 detik |
| Scheduler lama, 100 task, 6 container (pembanding) | Makespan 10,37 s, RU 83,97%, DI 0,2290 |
| Scheduler baru, `--check` ke 6 container | 6 OK, `ITER_PER_MI = 20` |
| Sesi 1: `--run-ids 1` untuk 100/200/300 | 3 run tersimpan, validasi OK |
| Sesi 2: `--resume --run-ids 2`, `host2-small` dimatikan saat run 200 | Run 100 tersimpan. Run 200 terdeteksi gagal (`RemoteDisconnected`), tidak masuk ringkasan, 166 task yang sempat selesai disimpan di `gagal/`, program berhenti dengan kode 2 |
| Sesi 3: container dinyalakan lagi, `--resume` | 5 run sisanya selesai, total 9 dari 9 |
| `--status` dan `--verifikasi` | 9/9 selesai, `VERIFIKASI OK` |
| File format lama di `scheduler/hasil/` (dari scheduler lama) | sha256 tidak berubah setelah 3 sesi |
| Menjalankan ulang nama eksperimen yang sama tanpa `--resume` | Ditolak, tidak ada file berubah |
| 1.000 task, 1 run | Makespan 93,54 s, RU 97,75%, DI 0,0191, validasi OK |

Hasil 100 task scheduler baru (10,04 / 12,11 / 9,80 s) sejalan dengan scheduler lama (10,37 s)
di lingkungan yang sama, jadi perubahan tidak menambah overhead yang berarti.


Yang diuji oleh unit test, antara lain:

- Dataset: N baris pertama dalam urutan asli, input identik antar-panggilan, N = 1.000 berisi
  semua task, N > 1.000 ditolak, header dilewati, sha256 dataset.
- OLB: setiap task dikirim tepat sekali, semua worker mendapat task, satu worker tidak pernah
  mengerjakan dua task bersamaan, pemecah seri awal mengikuti urutan daftar, scheduler menunggu
  saat semua worker sibuk.
- Kegagalan worker: respons tanpa `exec_time`, HTTP 500, `task_id` salah, `exec_time` negatif atau
  bukan angka, respons bukan JSON, koneksi ditutup, timeout, port tertutup, semua worker gagal.
  Semuanya terdeteksi, tidak macet, dan task gagal tidak tercatat sebagai berhasil.
- `/info`: `ITER_PER_MI` salah, identitas worker salah, worker tidak bisa dihubungi.
- Validasi: task hilang, duplikat, length salah, worker tidak dikenal, tumpang tindih pada satu
  worker, `exec_time` lebih besar dari waktu tunggu, makespan tidak valid, RU > 100%.
- Program utuh: file dan laporan terbentuk, tidak menimpa eksperimen yang ada, file lama di
  folder induk tidak tersentuh, sesi per nomor run dan resume, penolakan resume dengan
  konfigurasi berbeda, kegagalan di tengah lalu resume, file task yatim dipindah (tidak ditimpa),
  `--verifikasi` mendeteksi ringkasan yang diubah, `--status`.
- Rumus metrik dicek dengan contoh hitungan manual.

## 5. Yang belum dapat diverifikasi tanpa Azure atau laptop

- Program belum dijalankan di **Windows**. Kode memakai fitur Python standar yang lintas
  platform, tetapi perlu dibuktikan lewat PANDUAN_PENGUJIAN.md bagian A.
- Build image dengan `python:3.12-slim` asli dari Docker Hub (dilakukan di laptop).
- Koneksi scheduler ke worker host2 melalui jaringan privat Azure dengan kode baru.
- Versi Python di host1 (Ubuntu 24.04 bawaan 3.12; kode tidak memakai fitur di atas 3.8).
- Durasi sesi sebenarnya di Azure. Perkiraan sekitar 12 menit per sesi berasal dari kecepatan
  run historis, bukan dari pengukuran baru.
- Pemakaian CPU credits selama sesi. Angka sekitar 1,15 kredit per menit per host adalah
  perhitungan dari rumus Microsoft, bukan pengamatan.
- Apakah CSV mentah pengujian 26 Oktober masih ada di host1.
- Aturan NSG kedua VM saat ini.
- Hasil pengujian 400 sampai 1.000 task di Azure: **belum ada**.

## 6. Langkah berikutnya

1. Jalankan PANDUAN_PENGUJIAN.md bagian A di laptop dan laporkan hasilnya.
2. Evaluasi bersama sebelum masuk Azure.
3. Backup hasil lama di host1 (bagian B), update scheduler (bagian C), daftar periksa (bagian D).
4. Benchmark 3 sesi (bagian E), ambil hasil dan Stop (deallocate) VM (bagian F).
5. Setelah sekuensial selesai: bahas random sampling sebagai tahap 2.
