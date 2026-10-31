# Hasil Pengujian di Azure

## 1. Pengujian sekuensial 30 run (31 Oktober 2026)

Folder: `sequential/azure-seq-30run/`

- 100, 200, ..., 1.000 task, masing-masing 3 run, dibagi 3 sesi (run 1, run 2, run 3).
- Semua 30 run lolos validasi otomatis.
- Isi folder: `HASIL_REALWORLD.md` (laporan), `ringkasan.csv`, `kinerja_worker.csv`, `log_sesi.csv`,
  `konfigurasi.json`, dan `tasks/` (log tiap task).
- Grafik dan analisis ada di [README utama](../README.md) bagian 9 sampai 11.

## 2. Hasil historis (26 Oktober 2026)

- [HASIL_REALWORLD.md](HASIL_REALWORLD.md): ringkasan 100, 200, dan 300 task x 3 run, dijalankan dengan
  scheduler versi sebelum revisi. File dibiarkan apa adanya. "6 VM" di file itu maksudnya 6 worker.
- Data mentahnya (`ringkasan.csv` dan `tasks_<N>_run<R>.csv`) sudah diunduh dari host1 dan disimpan
  oleh tim, tetapi tidak dimasukkan ke repository ini.
