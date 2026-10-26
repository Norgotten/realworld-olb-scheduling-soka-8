# PENCERDASAN: Cara Setup OLB Real World (Azure + Docker)

Panduan langkah demi langkah dari nol: uji di laptop, membuat infrastruktur di Azure,
menjalankan ujicoba, sampai mematikan VM. Penjelasan cara kerja kode ada di
[PENCERDASAN2.md](PENCERDASAN2.md).

Target infrastruktur: **1 datacenter, 2 host, 3 VM per host (total 6 VM)**.

---

## 0. Gambaran besar

```
Azure, region Indonesia Central (Datacenter = Resource Group "soka8-dc")
 |
 |  jaringan internal: vnet-indonesiacentral-1 (172.16.0.0/24)
 |
 |-- VM Azure "host1" (B2als_v2, 2 vCPU, 4 GB)  = HOST 1   private IP 172.16.0.4
 |     |-- container host1-large   (jatah 1.0 CPU)   port 5001
 |     |-- container host1-medium  (jatah 0.5 CPU)   port 5002
 |     |-- container host1-small   (jatah 0.25 CPU)  port 5003
 |     '-- scheduler OLB (program Python yang bagi-bagi task)
 |
 '-- VM Azure "host2" (B2als_v2, 2 vCPU, 4 GB)  = HOST 2   private IP 172.16.0.5
       |-- container host2-large   (jatah 1.0 CPU)   port 5001
       |-- container host2-medium  (jatah 0.5 CPU)   port 5002
       '-- container host2-small   (jatah 0.25 CPU)  port 5003
```

| Di CloudSim | Di real world |
|---|---|
| Datacenter | Resource Group `soka8-dc` di Azure |
| Host | VM Azure (host1, host2) |
| VM (Large/Medium/Small) | Container Docker dengan batas CPU berbeda |
| Cloudlet (panjang MI dari GoCJ) | Task yang benar-benar membuat CPU sibuk, sebanding dengan MI |
| `applyOlbScheduling` | `scheduler/olb_scheduler.py` |
| Makespan hasil hitungan | Makespan waktu nyata (detik) |

Cara kerja OLB di sini: scheduler mengirim tiap task ke VM yang **paling awal siap**
(menganggur paling dulu). VM dianggap siap saat ia benar-benar selesai mengerjakan task
sebelumnya, bukan dari hitungan rumus seperti di CloudSim.

Kenapa container, bukan VM Azure langsung? Di Azure kita tidak bisa memilih server fisik
tempat VM ditaruh, jadi "host" tidak bisa ditunjukkan. Solusinya, VM Azure dijadikan host,
lalu di dalamnya dijalankan beberapa container (virtualisasi ringan) sebagai VM.

---

## 1. Isi folder

```
olb-realworld/
|-- README.md                  <- gambaran singkat proyek
|-- PENCERDASAN.md             <- panduan setup ini
|-- PENCERDASAN2.md            <- penjelasan cara kerja kode
|-- docker-compose.yml         <- khusus uji coba di laptop (6 VM sekaligus)
|-- hasil-azure/               <- hasil ujicoba di Azure
|-- worker/
|   |-- worker.py              <- program "VM": terima task, kerjakan, lapor
|   |-- Dockerfile             <- resep membungkus worker.py jadi container
|   '-- start_host.sh          <- menyalakan 3 VM di satu host Azure
'-- scheduler/
    |-- olb_scheduler.py       <- algoritma OLB + hitung metrik
    |-- workers.local.json     <- daftar VM untuk uji di laptop
    |-- workers.azure.json     <- daftar VM untuk di Azure (sudah diisi)
    '-- dataset/GoCJ_Dataset_1000.csv
```

Semua kode cuma pakai library bawaan Python. Tidak perlu `pip install`.

---

## 2. Tahap A: Coba di laptop dulu (gratis)

Tujuannya memastikan semua jalan sebelum kredit Azure kepotong.

**Yang dibutuhkan:** Docker Desktop dan Python 3 terpasang di laptop.

1. Buka terminal (PowerShell kalau Windows) di folder `olb-realworld`.
2. Nyalakan 6 VM:
   ```
   docker compose up -d --build
   ```
   Cek: `docker ps` harus menampilkan 6 container.
3. Masuk ke folder scheduler dan cek koneksi:
   ```
   cd scheduler
   python olb_scheduler.py --workers workers.local.json --check
   ```
   (Di Mac/Linux pakai `python3`.) Harus muncul 6 baris `OK`.
4. Jalankan uji kecil:
   ```
   python olb_scheduler.py --workers workers.local.json --sizes 100 --runs 1
   ```
   Hasil muncul di layar dan tersimpan di folder `scheduler/hasil/`.
   Cost akan 0 karena harga laptop memang diset 0.
5. Kalau sudah, matikan: `docker compose down` (dari folder `olb-realworld`).

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

### B2. Buat Resource Group (= datacenter)

1. Cari **Resource groups**, klik **Create**.
2. Subscription: Azure for Students. Nama: `soka8-dc`. Region: bebas.
3. **Review + create**, lalu **Create**.

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

Kenapa B2als_v2: ukuran 2 vCPU dengan RAM 4 GB, harga sekitar **$0.0428/jam**.
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
jadi VM wajib di-Stop manual (lihat B10).

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

### C1. Kirim folder proyek ke kedua host

Buka terminal di folder yang **berisi** folder `olb-realworld` (bukan di dalamnya).
Di Windows: buka folder itu di File Explorer, klik kanan area kosong, pilih
**Open in Terminal**.

```
scp -r olb-realworld azureuser@PUBLIC_IP_HOST1:~/
scp -r olb-realworld azureuser@PUBLIC_IP_HOST2:~/
```

Kalau folder `scheduler/hasil` dari Tahap A ikut terkirim, tidak masalah.

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

Nyalakan 3 VM:
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

### C4. Cek koneksi ke 6 VM (dari host1)

```
ssh azureuser@PUBLIC_IP_HOST1
cd ~/olb-realworld/scheduler
python3 olb_scheduler.py --workers workers.azure.json --check
```
Harus muncul 6 baris `OK`: 3 dari host1, 3 dari host2.

### C5. Jalankan ujicoba

Masih di host1:
```
python3 olb_scheduler.py --workers workers.azure.json --sizes 100 200 300 --runs 3
```
Totalnya 9 run, biasanya beberapa menit. Hasil tersimpan di
`~/olb-realworld/scheduler/hasil/`:
- `HASIL_REALWORLD.md`: tabel rata-rata dan detail tiap run
- `ringkasan.csv`: angka tiap run (bisa dibuka di Excel untuk grafik)
- `tasks_<N>_run<R>.csv`: log tiap task (task ke VM mana, mulai, selesai)

Untuk bukti laporan, screenshot: terminal C4 dan C5, `sudo docker ps` di tiap host,
dan halaman Virtual machines di Azure Portal yang menampilkan host1 dan host2.

Keluar: `exit`

### C6. Ambil hasil ke laptop

Pastikan sudah `exit` dari host1. Tulisan di depan terminal harus **bukan**
`azureuser@host1` lagi (di Windows jadi `PS C:\...>`). Kalau perintah ini dijalankan
masih di dalam host1, file hanya tersalin di host1 sendiri, tidak sampai ke laptop.

Dari terminal laptop:
```
scp -r azureuser@PUBLIC_IP_HOST1:~/olb-realworld/scheduler/hasil ./hasil-azure
```
Folder `hasil-azure` muncul di folder tempat terminal laptop sedang berada.
Di Windows bisa langsung dibuka dengan `explorer hasil-azure`.

### C7. MATIKAN VM (wajib tiap selesai sesi)

- Azure Portal, buka **host1**, klik **Stop**. Ulangi untuk **host2**.
  Status harus **Stopped (deallocated)**. Mematikan lewat `sudo shutdown` dari dalam VM
  **tidak** menghentikan tagihan.
- Menyalakan lagi: klik **Start**. Container otomatis ikut nyala
  (sudah diset `--restart unless-stopped`). Public IP bisa berubah, cek lagi di halaman VM.
- Tugas sudah dinilai: hapus resource group `soka8-dc` (Resource groups, pilih,
  **Delete resource group**). Semua isinya ikut terhapus dan tagihan berhenti total.

---

## 5. Penjelasan metrik

| Metrik | Arti | Rumus |
|---|---|---|
| Makespan | Waktu dari task pertama dikirim sampai task terakhir selesai | `max(finish)` |
| Cost | Biaya sewa semua host selama makespan | `jumlah harga per jam host x makespan / 3600` |
| Resource Utilization | Seberapa sibuk VM rata-rata selama makespan | `total waktu sibuk semua VM / (jumlah VM x makespan) x 100%` |
| Degree of Imbalance | Seberapa timpang beban antar VM | `(sibuk VM tersibuk - sibuk VM tersantai) / rata-rata sibuk` |

Rumus RU dan DI sama dengan versi CloudSim, jadi hasilnya bisa dibandingkan.

---

## 6. Faktor skala

Panjang task GoCJ 15.000 sampai 900.000 MI. Kalau dikerjakan apa adanya, satu run bisa
berjam-jam. Karena itu tiap 1 MI diubah jadi `ITER_PER_MI` putaran loop (default **20**).
Semua task diskala dengan faktor yang sama, jadi perbandingan besar kecilnya tetap.

Mau task lebih lama? Nyalakan ulang kedua host dengan nilai yang sama, misalnya:
```
ITER_PER_MI=50 bash ~/olb-realworld/worker/start_host.sh host1
```

---

## 7. Kalau ada masalah

| Masalah | Solusi |
|---|---|
| Semua size VM abu-abu / NotAvailableForSubscription | Region tidak diizinkan untuk akun student. Lihat B3, pilih region dari daftar. |
| `ssh` atau `scp` timeout | VM harus status Running. Cek Public IP terbaru (bisa berubah setelah Stop/Start). |
| `Permission denied, please try again` | Password salah. Ketik ulang pelan-pelan (memang tidak terlihat). |
| `$'\r': command not found` saat `start_host.sh` | File berubah format Windows. Jalankan `sed -i 's/\r$//' ~/olb-realworld/worker/start_host.sh` lalu ulangi. |
| `--check` GAGAL untuk VM host2 | Private IP di `workers.azure.json` harus sama dengan host2. Host2 harus satu VNet dengan host1. Cek `sudo docker ps` di host2. |
| `--check` GAGAL untuk VM host1 | Jalankan lagi `start_host.sh host1`, lalu `sudo docker ps`. |
| `permission denied` saat docker | Tambahkan `sudo` di depan perintah docker. |
| Di laptop, `docker compose` error | Buka Docker Desktop dulu, tunggu statusnya running. |

---

## 8. Sebelum upload ke GitHub

Aman di-upload: semua kode, dataset, Private IP `172.16.0.x` (hanya berlaku di dalam
jaringan internal Azure, tidak bisa diakses dari internet), harga, nama region.

**Jangan** di-upload atau tampilkan: Public IP host, password VM, email/nomor HP,
Subscription ID, dan screenshot yang memuat hal-hal itu (sensor dulu).

---

## 9. Bahan untuk laporan / presentasi

- **Arsitektur**: gambar di bagian 0.
- **Bukti real world**: screenshot Azure Portal (host1 dan host2 di resource group
  `soka8-dc`, region Indonesia Central) dan `sudo docker ps` di tiap host.
- **Perbandingan dengan CloudSim**:
  - Di CloudSim, run 1, 2, 3 hasilnya identik karena simulasi deterministik.
  - Di real world, hasil sedikit berbeda tiap run karena jeda jaringan dan fluktuasi CPU.
    VM seri B juga bertipe *burstable* (CPU bisa naik turun), salah satu sumber variasi.
  - Di CloudSim ready time dihitung dari rumus, di real world ready time diamati langsung.
- **Pola OLB yang terlihat**: VM Large lebih cepat kosong, jadi otomatis kebagian task lebih
  banyak. Tapi OLB tetap mengirim task besar ke VM Small kalau kebetulan Small yang sedang
  kosong, karena OLB tidak melihat kecepatan VM. Akibatnya di akhir sering ada VM Small yang
  masih mengerjakan task besar sendirian sementara yang lain sudah selesai, dan ini yang
  memperbesar makespan. Lihat kolom jumlah task per VM dan file `tasks_*.csv`.
