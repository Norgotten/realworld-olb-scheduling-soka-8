# PENCERDASAN: Cara Setup OLB Real World (Azure + Docker)

Panduan langkah demi langkah dari nol: uji di laptop, membuat infrastruktur di Azure,
menjalankan pengujian, sampai mematikan VM. Penjelasan cara kerja kode ada di
[PENCERDASAN2.md](PENCERDASAN2.md). Prosedur pengujian sekuensial 30 run (termasuk backup
hasil lama dan pemantauan CPU credits) ada di [PANDUAN_PENGUJIAN.md](PANDUAN_PENGUJIAN.md).

Target infrastruktur: **2 host (Azure VM), 3 worker (Docker container) per host, total 6 worker**.

---

## 0. Gambaran besar

```
Microsoft Azure, region Indonesia Central   (resource group "soka8-dc")
 |
 |  jaringan privat: vnet-indonesiacentral-1 (172.16.0.0/24)
 |
 |-- Azure VM "host1" (B2als_v2, 2 vCPU, 4 GiB) = HOST 1   private IP 172.16.0.4
 |     |-- worker host1-large   (Docker container, batas 1,0 CPU)   port 5001
 |     |-- worker host1-medium  (Docker container, batas 0,5 CPU)   port 5002
 |     |-- worker host1-small   (Docker container, batas 0,25 CPU)  port 5003
 |     '-- scheduler OLB (proses Python yang membagi task)
 |
 '-- Azure VM "host2" (B2als_v2, 2 vCPU, 4 GiB) = HOST 2   private IP 172.16.0.5
       |-- worker host2-large   (Docker container, batas 1,0 CPU)   port 5001
       |-- worker host2-medium  (Docker container, batas 0,5 CPU)   port 5002
       '-- worker host2-small   (Docker container, batas 0,25 CPU)  port 5003
```

Istilah yang dipakai di seluruh dokumen:

| Istilah | Arti di proyek ini |
|---|---|
| Azure VM / host | Mesin virtual Azure `host1` dan `host2`, host eksekusi pada arsitektur proyek |
| Worker | Docker container (`host1-large`, dst.), representasi logis VM CloudSim. **Bukan** VM sungguhan |
| Scheduler | Proses Python `olb_scheduler.py` yang berjalan di host1 |
| Azure Region | Lokasi deployment (Indonesia Central), bukan bukti satu datacenter fisik tertentu |
| Resource group | Pengelompokan logis resource Azure (`soka8-dc`) |

Pemetaan dari CloudSim (analogi, tidak identik):

| Di CloudSim | Di real world |
|---|---|
| Datacenter | Tidak ada padanan fisik yang pasti; resource berada di region Indonesia Central dan dikelompokkan di resource group `soka8-dc` |
| Host | Azure VM (host1, host2) |
| VM (Large/Medium/Small) | Worker: Docker container dengan batas CPU berbeda |
| Cloudlet (panjang MI dari GoCJ) | Task yang dijalankan sebagai beban CPU sintetis, sebanding dengan MI |
| `applyOlbScheduling` | `run_olb` di `scheduler/olb_scheduler.py` |
| Makespan hasil model | Makespan waktu nyata (detik) |

Cara kerja OLB di sini: scheduler mengirim task ke worker yang sedang idle. Kalau ada beberapa
yang idle, dipilih yang **paling awal siap**. Worker dianggap siap saat responsnya diterima
scheduler, yaitu saat ia benar-benar selesai mengerjakan task sebelumnya, bukan dari hitungan
rumus seperti di CloudSim.

Kenapa container, bukan Azure VM langsung sebagai "VM"? Di Azure kita tidak bisa memilih server
fisik tempat VM ditaruh, jadi lapisan "host" tidak bisa ditunjukkan. Solusinya, Azure VM dijadikan
host, lalu di dalamnya dijalankan beberapa container dengan batas CPU berbeda sebagai worker.

---

## 1. Isi folder

```
olb-realworld/
|-- README.md                  <- gambaran proyek, metrik, keterbatasan
|-- PENCERDASAN.md             <- panduan setup ini
|-- PENCERDASAN2.md            <- penjelasan cara kerja kode
|-- PANDUAN_PENGUJIAN.md       <- uji lokal, backup, benchmark Azure 30 run
|-- REVISI_TAHAP1.md           <- daftar perubahan revisi tahap 1
|-- docker-compose.yml         <- khusus uji coba di laptop (6 worker sekaligus)
|-- hasil-azure/               <- hasil pengujian di Azure (historis dan baru)
|-- worker/
|   |-- worker.py              <- program worker: terima task, kerjakan, lapor
|   |-- Dockerfile             <- resep membungkus worker.py jadi container
|   '-- start_host.sh          <- menyalakan 3 worker di satu Azure VM
'-- scheduler/
    |-- olb_scheduler.py       <- algoritma OLB, validasi, metrik, penyimpanan hasil
    |-- tests/                 <- unit test (tanpa Docker)
    |-- workers.local.json     <- daftar worker untuk uji di laptop
    |-- workers.azure.json     <- daftar worker di Azure (sudah diisi)
    '-- dataset/GoCJ_Dataset_1000.csv
```

Semua kode cuma pakai library bawaan Python. Tidak perlu `pip install`.

---

## 2. Tahap A: Coba di laptop dulu (gratis)

Tujuannya memastikan semua jalan sebelum kredit Azure terpakai. Langkah lengkapnya, termasuk
unit test, simulasi worker gagal, dan cek perlindungan data, ada di
[PANDUAN_PENGUJIAN.md](PANDUAN_PENGUJIAN.md) bagian A. Ringkasnya, dari folder `olb-realworld`:

```
docker compose up -d --build
python -m unittest discover -s scheduler/tests -v
cd scheduler
python olb_scheduler.py --workers workers.local.json --check
python olb_scheduler.py --workers workers.local.json --name uji-laptop --sizes 100 --runs 1
cd ..
docker compose down
```

Hasil tersimpan di `scheduler/hasil/sequential/uji-laptop/`. Cost akan 0 karena tarif laptop
memang diset 0. Kalau `python` tidak dikenal di Windows, pakai `py`.

---

## 3. Tahap B: Menyiapkan Azure (lewat Azure Portal)

Semua langkah ini di https://portal.azure.com dengan akun Azure for Students.

### B1. Pasang alarm budget

1. Ketik **Budgets** di kotak pencarian atas, buka, klik **+ Add**.
2. Halaman **Create a budget**:
   - Scope: biarkan (nama langganan kamu)
   - Filters: lewati
   - Name: `soka8-budget`
   - Reset period: **Monthly**
   - Creation date dan Expiration date: biarkan
   - Amount: `20`
   - Klik **Next >**
3. Halaman **Set alerts**:
   - Baris 1: Type **Actual**, % of budget `50` (email saat sudah $10)
   - Baris 2: Type **Actual**, % of budget `90` (email saat sudah $18)
   - Alert recipients (email): email kamu
   - Klik **Create**

Budget hanya mengirim email, tidak mematikan VM. Azure for Students tidak menagih uang
pribadi; kalau kredit habis, layanan hanya berhenti.

### B2. Buat Resource Group

1. Cari **Resource groups**, klik **Create**.
2. Subscription: Azure for Students. Nama: `soka8-dc`. Region: bebas.
3. **Review + create**, lalu **Create**.

Resource group adalah pengelompokan logis untuk resource Azure, bukan datacenter fisik.
Region resource group hanya tempat menyimpan catatan isinya. VM di dalamnya boleh
berada di region lain.

### B3. Cek region yang diizinkan untuk akun student

Akun Azure for Students hanya boleh membuat VM di beberapa region. Kalau di region lain
semua ukuran VM berwarna abu-abu ("NotAvailableForSubscription"), ini penyebabnya.

1. Cari **Policy**, buka menu **Assignments**.
2. Klik assignment bernama mirip **Allowed resource deployment regions**.
3. Lihat bagian **Parameters**. Untuk akun kami isinya:
   `indiasouthcentral, eastasia, malaysiawest, japaneast, indonesiacentral`.

Kami memakai **Indonesia Central** (Jakarta), yang paling dekat.

### B4. Buat VM Azure pertama (host1)

Cari **Virtual machines**, klik **Create**, pilih **Azure virtual machine**.

**Tab Basics**

| Kolom | Isi |
|---|---|
| Subscription | Azure for Students |
| Resource group | `soka8-dc` |
| Virtual machine name | `host1` |
| Region | **(Asia Pacific) Indonesia Central** |
| Availability options | **No infrastructure redundancy required** |
| Security type | Trusted launch virtual machines (biarkan) |
| Image | **Ubuntu Server 24.04 LTS - x64 Gen2** |
| VM architecture | x64 |
| Size | Klik **See all sizes**, ketik `B2` di kotak search, pilih **B2als_v2** (2 vCPU, 4 GB), lalu **Select** |
| Run with Azure Spot discount | Jangan dicentang |
| Authentication type | **Password** |
| Username | `azureuser` |
| Password | Password kuat, **catat** (pakai yang sama untuk host2) |
| Public inbound ports | Allow selected ports |
| Select inbound ports | **SSH (22)** (peringatan kuning normal) |

Kenapa B2als_v2: ukuran 2 vCPU dengan RAM 4 GB, estimasi harga di Azure Portal saat VM dibuat
sekitar **$0.0428/jam**. Nilai ini dipakai sebagai asumsi tarif untuk Estimated Compute Cost.
B2als_v2 bersifat burstable (performa dasar 30%, memakai CPU credits); lihat
[PANDUAN_PENGUJIAN.md](PANDUAN_PENGUJIAN.md) bagian E1.
Ukuran 1 GB (B2ats_v2) lebih murah tapi RAM terlalu mepet.

**Tab Disks**

| Kolom | Isi |
|---|---|
| Encryption at host | Kosong |
| OS disk size | Image default (30 GiB) |
| OS disk type | Premium SSD (default) atau Standard SSD kalau mau lebih hemat |
| Delete with VM | **Dicentang** |
| Lainnya | Biarkan |

Catatan: disk tetap ditagih kecil walau VM di-Stop. Hapus resource group setelah tugas selesai.

**Tab Networking**

| Kolom | Isi |
|---|---|
| Virtual network | (new) `vnet-indonesiacentral-1` (biarkan, **catat namanya**) |
| Subnet | (new) `snet-indonesiacentral-1 - 172.16.0.0/24` |
| Public IP | (new) `host1-ip` |
| NIC network security group | Basic |
| Public inbound ports | Allow selected ports, SSH (22) |
| Delete public IP and NIC when VM is deleted | **Dicentang** |
| Enable accelerated networking | Kosong |
| Load balancing options | None |

**Tab Management**

Biarkan semua default. Auto-shutdown **tidak didukung** di region Indonesia Central,
jadi VM wajib di-Stop manual (lihat C7).

**Review + create**

Lewati tab Monitoring, Advanced, Tags. Klik **Review + create**, tunggu
**Validation passed**, isi Preferred phone number dengan nomor HP sendiri kalau diminta,
lalu **Create**. Tunggu sampai **Your deployment is complete**, klik **Go to resource**.

### B5. Buat VM Azure kedua (host2)

Ulangi B4 dengan nilai yang sama, kecuali:

| Kolom | Isi |
|---|---|
| Virtual machine name | `host2` |
| Tab Networking, Virtual network | **`vnet-indonesiacentral-1` yang sudah ada** (tanpa tulisan "(new)") |
| Tab Networking, Subnet | `snet-indonesiacentral-1 - 172.16.0.0/24` |
| Tab Networking, Public IP | (new) `host2-ip` |

Di halaman Review, pastikan baris Virtual network **tidak** ada tulisan "(new)".
Kalau ada, host2 membuat jaringan baru dan tidak bisa dihubungi host1.

### B6. Catat alamat IP kedua host

Di halaman tiap VM, bagian **Networking**, catat di notepad pribadi
(**jangan** ditaruh di README atau GitHub):

```
host1  Public IP: ...........   Private IP: 172.16.0.4
host2  Public IP: ...........   Private IP: 172.16.0.5
username: azureuser   password: ...........
```

- **Public IP**: untuk masuk (SSH) dari laptop.
- **Private IP**: untuk host1 menghubungi host2 lewat jaringan internal.

Private IP host2 (`172.16.0.5`) dan harga per jam (`0.0428`) sudah diisi di
`scheduler/workers.azure.json`. Kalau milikmu berbeda, ubah file itu.

---

## 4. Tahap C: Menjalankan di Azure (lewat terminal)

Semua perintah di bawah diketik di PowerShell (Windows) atau Terminal (Mac/Linux).
Ganti `PUBLIC_IP_HOST1` dan `PUBLIC_IP_HOST2` dengan Public IP dari B6.

Hal yang perlu diketahui:
- Saat pertama kali konek, muncul `Are you sure you want to continue connecting (yes/no)?`.
  Ketik `yes`, Enter.
- Saat mengetik password, layar **tidak menampilkan apa pun**. Itu normal, ketik saja lalu Enter.

### C1. Kirim folder proyek ke kedua host (hanya saat setup pertama)

Langkah ini hanya untuk host yang **belum** punya folder `olb-realworld`. Untuk memperbarui
scheduler di host1 yang sudah berisi hasil pengujian, **jangan** pakai `scp -r` seluruh folder,
karena folder `scheduler/hasil` dari laptop bisa menimpa file di host1. Ikuti
[PANDUAN_PENGUJIAN.md](PANDUAN_PENGUJIAN.md) bagian B (backup) dan C (update scheduler).

Buka terminal di folder yang **berisi** folder `olb-realworld` (bukan di dalamnya).
Di Windows: buka folder itu di File Explorer, klik kanan area kosong, pilih
**Open in Terminal**. Hapus dulu folder `olb-realworld/scheduler/hasil` di laptop kalau ada,
supaya hasil uji laptop tidak ikut terkirim.

```
scp -r olb-realworld azureuser@PUBLIC_IP_HOST1:~/
scp -r olb-realworld azureuser@PUBLIC_IP_HOST2:~/
```

### C2. Siapkan host1

Masuk ke host1:
```
ssh azureuser@PUBLIC_IP_HOST1
```
Tanda berhasil: tulisan di terminal berubah jadi `azureuser@host1:~$`. Mulai sekarang
perintah dijalankan di komputer Azure, bukan laptop.

Install Docker (sekali saja, 1 sampai 2 menit):
```
curl -fsSL https://get.docker.com | sudo sh
```

Nyalakan 3 worker:
```
bash ~/olb-realworld/worker/start_host.sh host1
```
Di akhir harus tampil 3 container: `host1-large`, `host1-medium`, `host1-small`.

Keluar: `exit`

### C3. Siapkan host2

```
ssh azureuser@PUBLIC_IP_HOST2
curl -fsSL https://get.docker.com | sudo sh
bash ~/olb-realworld/worker/start_host.sh host2
exit
```
Harus tampil `host2-large`, `host2-medium`, `host2-small`.

### C4. Cek koneksi ke 6 worker (dari host1)

```
ssh azureuser@PUBLIC_IP_HOST1
cd ~/olb-realworld/scheduler
python3 olb_scheduler.py --workers workers.azure.json --check
```
Harus muncul 6 baris `OK` (3 dari host1, 3 dari host2), masing-masing dengan `iter_per_mi=20`,
diakhiri `Semua 6 worker siap, ITER_PER_MI = 20.` Scheduler juga memeriksa bahwa nama dan host
setiap worker sesuai dengan `workers.azure.json`.

### C5. Jalankan pengujian

Prosedur pengujian sekuensial 30 run (3 sesi, pemantauan CPU credits, tmux, dan penanganan
kegagalan) ada di [PANDUAN_PENGUJIAN.md](PANDUAN_PENGUJIAN.md) bagian D dan E.

Sebagai catatan sejarah, pengujian 26 Oktober 2026 (100/200/300 task x 3) dijalankan dengan
scheduler versi sebelum revisi memakai perintah:
```
python3 olb_scheduler.py --workers workers.azure.json --sizes 100 200 300 --runs 3
```
Versi lama itu menulis langsung ke `scheduler/hasil/` dan menimpa file dengan nama yang sama.
Versi sekarang menulis ke `scheduler/hasil/sequential/<nama-eksperimen>/` dan tidak pernah
menimpa file yang sudah ada.

Untuk bukti laporan, screenshot: terminal `--check` dan sesi pengujian, `sudo docker ps` di tiap
host, halaman Virtual machines di Azure Portal yang menampilkan host1 dan host2, dan grafik
CPU Credits Remaining kedua host.

### C6. Ambil hasil ke laptop

Pastikan sudah `exit` dari host1. Tulisan di depan terminal harus **bukan**
`azureuser@host1` lagi (di Windows jadi `PS C:\...>`). Kalau perintah ini dijalankan
masih di dalam host1, file hanya tersalin di host1 sendiri, tidak sampai ke laptop.

Dari terminal laptop (ganti `azure-seq-30run` dengan nama eksperimen):
```
scp -r azureuser@PUBLIC_IP_HOST1:~/olb-realworld/scheduler/hasil/sequential/azure-seq-30run ./
```
Di Windows bisa langsung dibuka dengan `explorer azure-seq-30run`.

### C7. MATIKAN VM (wajib tiap selesai sesi)

- Azure Portal, buka **host1**, klik **Stop**. Ulangi untuk **host2**.
  Status harus **Stopped (deallocated)**. Mematikan lewat `sudo shutdown` dari dalam VM
  **tidak** menghentikan tagihan komputasi.
- Menyalakan lagi: klik **Start**. Container otomatis ikut nyala
  (sudah diset `--restart unless-stopped`). Public IP bisa berubah, cek lagi di halaman VM.
  Cek juga Private IP host2 tetap `172.16.0.5`, lalu jalankan `--check`.
- Tugas sudah dinilai: hapus resource group `soka8-dc` (Resource groups, pilih,
  **Delete resource group**). Semua isinya ikut terhapus dan tagihan berhenti total.

---

## 5. Penjelasan metrik

| Metrik | Arti | Rumus |
|---|---|---|
| Makespan (detik) | Durasi dari awal penjadwalan batch sampai respons task terakhir diterima scheduler, termasuk overhead komunikasi | `max(finish)` |
| Estimated Compute Cost (USD) | Estimasi biaya komputasi dua host selama makespan. Bukan tagihan Azure sebenarnya | `(tarif host1 + tarif host2) x makespan / 3600` |
| Resource Utilization (%) | Utilisasi waktu kerja worker. Bukan `Percentage CPU` Azure | `total waktu eksekusi semua worker / (jumlah worker x makespan) x 100` |
| Degree of Imbalance | Ketimpangan total waktu sibuk antarworker (0 = merata) | `(sibuk maks - sibuk min) / rata-rata sibuk` |

Bentuk rumus RU dan DI mengikuti versi CloudSim, tetapi angka mentahnya tidak dibandingkan
langsung karena cara mengukur waktu berbeda. Rumus biaya juga berbeda: CloudSim memakai tarif
per tipe VM dikali waktu eksekusi, versi real world memakai tarif dua Azure VM selama makespan.
Penjelasan lengkap ada di [README.md](README.md) bagian 7.

---

## 6. Faktor skala `ITER_PER_MI`

Panjang task GoCJ 15.000 sampai 900.000 MI. Tiap 1 MI diubah menjadi `ITER_PER_MI` iterasi
fungsi `burn_cpu()`, dengan nilai **20** untuk seluruh eksperimen proyek ini. Rasio beban
antartask tetap sama dengan dataset, tetapi jumlah instruksi mesin tidak sama dengan MI di
CloudSim. Nilai 20 belum dikalibrasi; alasannya dipertahankan dijelaskan di
[README.md](README.md) bagian 5.

Scheduler menolak berjalan kalau ada worker yang memakai nilai selain 20
(opsi `--expect-iter`, default 20). Jangan mengubah `ITER_PER_MI` untuk eksperimen ini, karena
hasilnya tidak akan konsisten dengan pengujian sebelumnya.

---

## 7. Kalau ada masalah

| Masalah | Solusi |
|---|---|
| Semua size VM abu-abu / NotAvailableForSubscription | Region tidak diizinkan untuk akun student. Lihat B3, pilih region dari daftar. |
| `ssh` atau `scp` timeout | VM harus status Running. Cek Public IP terbaru (bisa berubah setelah Stop/Start). |
| `Permission denied, please try again` | Password salah. Ketik ulang pelan-pelan (memang tidak terlihat). |
| `$'\r': command not found` saat `start_host.sh` | File berubah format Windows. Jalankan `sed -i 's/\r$//' ~/olb-realworld/worker/start_host.sh` lalu ulangi. |
| `--check` GAGAL untuk worker host2 | Private IP di `workers.azure.json` harus sama dengan host2. Host2 harus satu VNet dengan host1. Cek `sudo docker ps` di host2. |
| `--check` GAGAL untuk worker host1 | Jalankan lagi `start_host.sh host1`, lalu `sudo docker ps`. |
| `--check` melapor `ITER_PER_MI` atau nama worker salah | Jalankan ulang `start_host.sh` di host tersebut tanpa variabel `ITER_PER_MI`. |
| `permission denied` saat docker | Tambahkan `sudo` di depan perintah docker. |
| Di laptop, `docker compose` error | Buka Docker Desktop dulu, tunggu statusnya running. |
| Pesan lain dari scheduler | Lihat [PANDUAN_PENGUJIAN.md](PANDUAN_PENGUJIAN.md) bagian G. |

---

## 8. Keamanan jaringan

Worker memakai HTTP tanpa autentikasi di port 5001 sampai 5003. Ini cukup untuk eksperimen
selama port tersebut **hanya** dapat diakses dari jaringan privat Azure.

- Di Network Security Group kedua VM, yang terbuka ke internet hanya SSH (22).
- Jangan menambahkan aturan inbound untuk 5001 sampai 5003 dari `Any` atau `Internet`.
- Cara mengecek ada di [PANDUAN_PENGUJIAN.md](PANDUAN_PENGUJIAN.md) bagian D.

---

## 9. Sebelum upload ke GitHub

Aman di-upload: semua kode, dataset, Private IP `172.16.0.x` (hanya berlaku di dalam
jaringan internal Azure, tidak bisa diakses dari internet), harga, nama region.

**Jangan** di-upload atau tampilkan: Public IP host, password VM, email/nomor HP,
Subscription ID, dan screenshot yang memuat hal-hal itu (sensor dulu).

---

## 10. Bahan untuk laporan / presentasi

- **Arsitektur**: gambar di bagian 0.
- **Bukti real world**: screenshot Azure Portal (host1 dan host2 di resource group
  `soka8-dc`, region Indonesia Central), `sudo docker ps` di tiap host, dan grafik
  CPU Credits Remaining selama sesi pengujian.
- **Perbandingan dengan CloudSim**:
  - Di CloudSim, run 1, 2, 3 hasilnya identik karena simulasi deterministik.
  - Di real world, hasil berbeda tiap run walaupun input identik, karena jaringan, penjadwalan
    CPU, dan kondisi host. VM seri B bersifat burstable, salah satu sumber variasi.
  - Di CloudSim ready time dimodelkan, di real world ready time diamati dari respons worker.
  - Waktu absolut tidak dibandingkan langsung karena `ITER_PER_MI` tidak membuat jumlah
    instruksi sama dengan MI. Yang dibandingkan adalah pola.
- **Pola OLB yang terlihat**: worker Large lebih cepat kosong, jadi otomatis mendapat task lebih
  banyak. OLB tetap mengirim task besar ke worker Small kalau kebetulan Small yang sedang
  kosong, karena OLB tidak melihat kecepatan worker. Akibatnya di akhir sering ada worker Small
  yang masih mengerjakan task besar sendirian, dan ini memperbesar makespan. Karena worker
  hampir tidak pernah menganggur selama masih ada task, waktu sibuknya cenderung merata (DI
  rendah), sedangkan ketimpangan lebih terlihat pada jumlah task per worker. Lihat tabel
  jumlah task per worker di `HASIL_REALWORLD.md` dan file `tasks/tasks_*.csv`.
