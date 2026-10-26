#!/usr/bin/env bash
# Menyalakan 3 "VM" (container) di host ini.
# Pakai:  bash start_host.sh host1      (atau host2)
#
#   VM       jatah CPU   port
#   LARGE    1.0         5001
#   MEDIUM   0.5         5002
#   SMALL    0.25        5003
set -e
HOST="${1:?Kasih nama host, contoh: bash start_host.sh host1}"
ITER="${ITER_PER_MI:-20}"

cd "$(dirname "$0")"
sudo docker build -t olb-worker .

run_vm () {  # $1=tipe  $2=cpus  $3=port
  local name="${HOST}-$(echo "$1" | tr 'A-Z' 'a-z')"
  sudo docker rm -f "$name" >/dev/null 2>&1 || true
  # -d                      : jalan di latar belakang
  # --restart unless-stopped: otomatis nyala lagi saat host di-Start ulang
  # --cpus                  : jatah CPU = kecepatan VM
  # -p PORT_HOST:5000       : pintu di host diteruskan ke port 5000 di dalam container
  # -e                      : identitas VM yang dibaca worker.py
  sudo docker run -d --name "$name" --restart unless-stopped \
    --cpus "$2" -p "$3:5000" \
    -e VM_NAME="$name" -e HOST_NAME="$HOST" -e VM_TYPE="$1" \
    -e VM_CPUS="$2" -e ITER_PER_MI="$ITER" \
    olb-worker >/dev/null
  echo "  $name  (cpus=$2, port=$3)  nyala"
}

echo "Menyalakan VM di $HOST:"
run_vm LARGE  1.0  5001
run_vm MEDIUM 0.5  5002
run_vm SMALL  0.25 5003
sudo docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
