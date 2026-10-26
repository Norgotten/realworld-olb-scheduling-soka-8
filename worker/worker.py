"""
Worker = satu "VM" di dalam host.

Kerjanya sederhana:
  GET  /info  -> kirim identitas VM (nama, host, tipe, jatah CPU)
  POST /task  -> terima task {"task_id": .., "length": MI}, kerjakan, lalu lapor waktu eksekusi

Server ini sengaja single-threaded: satu VM hanya mengerjakan SATU task dalam satu waktu,
sama seperti asumsi OLB di CloudSim (task antre di VM).
Hanya pakai library bawaan Python, jadi tidak perlu pip install apa pun.
"""
import json
import os
import socket
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

VM_NAME = os.environ.get("VM_NAME", socket.gethostname())
HOST_NAME = os.environ.get("HOST_NAME", "unknown-host")
VM_TYPE = os.environ.get("VM_TYPE", "UNKNOWN")
VM_CPUS = os.environ.get("VM_CPUS", "?")
# Berapa iterasi loop untuk tiap 1 MI. Ini "faktor skala" agar task GoCJ
# (15.000 - 900.000 MI) selesai dalam hitungan detik, bukan jam.
ITER_PER_MI = int(os.environ.get("ITER_PER_MI", "20"))
# Di dalam container, worker selalu mendengarkan di port 5000.
# Docker yang menyambungkan port host (5001/5002/5003) ke port 5000 ini.
PORT = int(os.environ.get("PORT", "5000"))


def burn_cpu(iterations: int) -> int:
    """Bikin CPU sibuk sebanyak `iterations` putaran. Ini yang mewakili eksekusi task."""
    x = 0
    for i in range(iterations):
        x = (x + i * i) % 1_000_003
    return x


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/info":
            self._send(200, {
                "vm": VM_NAME, "host": HOST_NAME, "type": VM_TYPE,
                "cpus": VM_CPUS, "iter_per_mi": ITER_PER_MI,
            })
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/task":
            self._send(404, {"error": "not found"})
            return
        size = int(self.headers.get("Content-Length", 0))
        task = json.loads(self.rfile.read(size) or b"{}")
        length = int(task["length"])

        start = time.perf_counter()
        burn_cpu(length * ITER_PER_MI)
        exec_time = time.perf_counter() - start

        self._send(200, {
            "task_id": task.get("task_id"), "length": length,
            "vm": VM_NAME, "host": HOST_NAME, "type": VM_TYPE,
            "exec_time": exec_time,
        })

    def log_message(self, fmt, *args):
        pass  # matikan log per request biar terminal tidak penuh


if __name__ == "__main__":
    print(f"[{VM_NAME}] host={HOST_NAME} type={VM_TYPE} cpus={VM_CPUS} "
          f"iter_per_mi={ITER_PER_MI} siap di port {PORT}", flush=True)
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
