# OLB Task Scheduling: Implementasi Real World

Implementasi algoritma heuristik **Opportunistic Load Balancing (OLB)** di infrastruktur
cloud sungguhan (Microsoft Azure). Proyek ini lanjutan dari versi simulasi CloudSim
kelompok 8, untuk tugas mata kuliah SOKA.

## Tujuan

Membuktikan algoritma OLB yang sebelumnya diuji di simulator CloudSim juga bisa berjalan
di cloud nyata, lalu melihat bedanya: di simulator hasil selalu sama persis, di dunia nyata
ada pengaruh jaringan dan performa CPU yang naik turun.

## Arsitektur

```
Azure, region Indonesia Central  (1 datacenter)
 |
 |-- host1 (VM Azure B2als_v2)
 |     |-- VM host1-large   (jatah 1.0 CPU)
 |     |-- VM host1-medium  (jatah 0.5 CPU)
 |     |-- VM host1-small   (jatah 0.25 CPU)
 |     '-- scheduler OLB
 |
 '-- host2 (VM Azure B2als_v2)
       |-- VM host2-large   (jatah 1.0 CPU)
       |-- VM host2-medium  (jatah 0.5 CPU)
       '-- VM host2-small   (jatah 0.25 CPU)
```

- **Host**: 2 VM Azure
- **VM**: 6 container Docker (3 per host) dengan jatah CPU berbeda
- **Scheduler**: program Python yang membagi task dari dataset GoCJ ke VM dengan aturan OLB,
  yaitu task diberikan ke VM yang paling awal siap

## Cara menggunakan

Ringkasnya:

1. Buat 2 VM di Azure dalam satu jaringan.
2. Di tiap host, nyalakan 3 VM: `bash worker/start_host.sh host1` (atau `host2`).
3. Di host1, jalankan scheduler:
   ```
   cd scheduler
   python3 olb_scheduler.py --workers workers.azure.json --sizes 100 200 300 --runs 3
   ```
4. Hasil tersimpan di `scheduler/hasil/`.

Panduan lengkap dari nol (termasuk uji coba di laptop dan setup Azure) ada di
[PENCERDASAN.md](PENCERDASAN.md). Penjelasan cara kerja kode ada di
[PENCERDASAN2.md](PENCERDASAN2.md).

## Hasil

Rata-rata dari 3 run:

| Jumlah Task | Makespan (s) | Cost ($) | Resource Utilization (%) | Degree of Imbalance |
|---:|---:|---:|---:|---:|
| 100 | 13.51 | 0.00032 | 82.95 | 0.2901 |
| 200 | 22.29 | 0.00053 | 97.32 | 0.0377 |
| 300 | 37.87 | 0.00090 | 91.76 | 0.1131 |

Detail di [hasil-azure/](hasil-azure/).

## Dataset

GoCJ: Google Cloud Jobs Dataset. Hussain, A., & Aleem, M. (2018). Mendeley Data, V1.
DOI: [10.17632/b7bp6xhrcd.1](https://data.mendeley.com/datasets/b7bp6xhrcd/1). Lisensi CC BY 4.0.
