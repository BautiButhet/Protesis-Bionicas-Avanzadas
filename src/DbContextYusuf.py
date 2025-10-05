"""
Monitor de micro-desplazamiento -> versión segura para Supabase.
- MODE: 'simulate' | 'serial'
- Env vars required:
    SUPABASE_URL  -> https://<project-ref>.supabase.co
    SUPABASE_KEY  -> anon key (recommended) OR service_role for server-only scripts (not safe on clients)
"""

import os
import time
import math
import threading
import queue
import random
from datetime import datetime, timezone

from supabase import create_client
from collections import deque
from dotenv import load_dotenv

# ventana de integración (segundos)
ACC_WINDOW_SECONDS = 2.0
acc_window = deque()
# carga .env si existe
load_dotenv()  
# ----------------- CONFIG from ENV (secure) -----------------
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SENSOR_ID = int(os.environ.get("SENSOR_ID", "1")) # sensor id to reference
if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Set SUPABASE_URL and SUPABASE_KEY environment variables before running.")

sb = create_client(SUPABASE_URL, SUPABASE_KEY)

# $env:SUPABASE_URL  = "https://vnkbnqdjlgzwnzpsvxdd.supabase.co"
# $env:SUPABASE_KEY  = "TU_ANON_O_SERVICE_ROLE_KEY_AQUI"
# $env:SENSOR_ID     = "4"   
# o
# @"
# SUPABASE_URL=https://vnkbnqdjlgzwnzpsvxdd.supabase.co
# SUPABASE_KEY=TU_ANON_O_SERVICE_ROLE_KEY_AQUI
# SENSOR_ID=4
# "@ | Out-File -FilePath .env -Encoding utf8

# ----------------- GENERAL CONFIG -----------------
MODE = 'simulate'        # 'simulate' | 'serial'
SAMPLE_INTERVAL = 0.05   # s -> 20 Hz en simulate

# umbrales (µm)
THRESH_OK_UM = 50.0
THRESH_ALERT_UM = 150.0
SUSTAIN_SECONDS_MIN = 2.5

# buffer / retry policy
API_RETRY_PAUSE = 5.0    # wait seconds after API error before retrying
BATCH_INSERT_SIZE = 20

# ----------------- QUEUES / FLAGS -----------------
row_queue = queue.Queue(maxsize=20000)
insert_buffer = []              # buffered payloads when API failing
buffer_lock = threading.Lock()
stop_event = threading.Event()

# state
current_state = "OK"
warning_since = None
state_lock = threading.Lock()

# ----------------- PRODUCER SIMULATE -----------------
def producer_simulate(produce_interval=SAMPLE_INTERVAL,
                      total_disp_target_um=150.0,
                      total_time_s=10.0):
    g = 9.81
    x_m = total_disp_target_um * 1e-6
    a_const = 2.0 * x_m / (total_time_s * total_time_s)
    print(f"SIM Producer: target {total_disp_target_um}µm in {total_time_s}s -> a_const={a_const:.3e} m/s^2")
    while not stop_event.is_set():
        ts = time.time()
        ax = random.gauss(0.0, 5e-4)
        ay = random.gauss(0.0, 5e-4)
        az = g + a_const + random.gauss(0.0, 5e-4)
        try:
            row_queue.put([ts, ax, ay, az], timeout=0.3)
        except queue.Full:
            pass
        time.sleep(produce_interval)

# ----------------- SERIAL PRODUCER -----------------
def producer_serial(port='COM3', baud=115200):
    try:
        import serial as pyserial
    except Exception:
        print("pyserial no instalado. Cambia a MODE='simulate' o instala pyserial.")
        return
    try:
        ser = pyserial.Serial(port, baud, timeout=1.0)
        time.sleep(0.05)
    except Exception as e:
        print("Error abriendo puerto serial:", e)
        return
    print(f"Serial producer on {port}@{baud}")
    while not stop_event.is_set():
        try:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            if not line:
                continue
            parts = line.split(',')
            if len(parts) < 4:
                continue
            ts_us = float(parts[0]); ts = ts_us / 1e6
            ax = float(parts[1]); ay = float(parts[2]); az = float(parts[3])
            try:
                row_queue.put([ts, ax, ay, az], timeout=0.3)
            except queue.Full: pass
        except Exception as e:
            print("Error leyendo serial:", e)
            time.sleep(0.05)
    try: ser.close()
    except: pass

# ----------------- ALERTS & EVENTS -----------------
def _insert_event(payload):
    """Try to insert an event into supabase; if fails, buffer it."""
    try:
        r = sb.table("eventos").insert(payload).execute()
        return True
    except Exception as e:
        print("Error inserting event to Supabase:", e)
        # buffer it
        with buffer_lock:
            insert_buffer.append(("eventos", payload))
        return False

def alert_and_log(desp_um, ts_s):
    global current_state, warning_since
    with state_lock:
        if desp_um > THRESH_ALERT_UM:
            if current_state != "ALERT":
                current_state = "ALERT"
                warning_since = None
                msg = f"ALERTA INMEDIATA: disp {desp_um:.1f} µm > {THRESH_ALERT_UM} µm"
                print(f"[{datetime.fromtimestamp(ts_s)}] {msg}")
                # _insert_event({
                #     "sensor_id": SENSOR_ID,
                #     "ts_s": ts_s,
                #     "disp_um": disp_um,
                #     "level": "ALERT",
                #     "message": msg
                # })
            return "ALERT"
        if desp_um > THRESH_OK_UM:         
                if current_state != "WARNING":
                        current_state = "WARNING"
                        msg = f"ADVERTENCIA: disp {desp_um:.1f} µm"
                        print(f"[{datetime.fromtimestamp(ts_s)}] {msg}")
                        # _insert_event({
                        #     "sensor_id": SENSOR_ID,
                        #     "ts_s": ts_s,
                        #     "disp_um": disp_um,
                        #     "level": "WARNING",
                        #     "message": msg
                        # })
                return "WARNING"
        else:
            if current_state != "OK":
                msg = f"VOLVIO A OK: disp {desp_um:.1f} µm"
                print(f"[{datetime.fromtimestamp(ts_s)}] {msg}")
                # _insert_event({
                
                #     "sensor_id": SENSOR_ID,
                #     "ts_s": ts_s,
                #     "disp_um": disp_um,
                #     "level": "OK",
                #     "message": msg
                # })
            warning_since = None
            current_state = "OK"
            return "OK"


# ----------------- WRITER (procesa y envia lecturas) -----------------
def safe_insert(table, payload):
    """Try single insert; on failure buffer it."""
    global api_ok, last_api_error_time
    try:
        r = sb.table(table).insert(payload).execute()
        api_ok = True
        return True
    except Exception as e:
        print(f"Error inserting to Supabase ({table}):", e)
        api_ok = False
        last_api_error_time = time.time()
        with buffer_lock:
            insert_buffer.append((table, payload))
        return False


# ---------- CÁLCULOS ----------

def compute_desp_from_acc_window(ts, ax, ay, az):
    """
    Integra en ventana de tiempo para estimar desplazamiento pico-a-pico (µm).
    """
    acc_window.append((ts, ax, ay, az))
    while acc_window and (ts - acc_window[0][0] > ACC_WINDOW_SECONDS):
        acc_window.popleft()

    if len(acc_window) < 3:
        return 0.0

    xs  = [r[0] for r in acc_window]
    axs = [r[1] for r in acc_window]
    ays = [r[2] for r in acc_window]
    azs = [r[3] for r in acc_window]

    # magnitud de aceleración
    mags = [math.sqrt(axs[i]**2 + ays[i]**2 + azs[i]**2) for i in range(len(xs))]

    # quitar bias (gravedad + drift DC)
    mean_a = sum(mags)/len(mags)
    mags = [m - mean_a for m in mags]

    # integrar aceleración -> velocidad
    vs = [0.0]*len(mags)
    for i in range(1, len(mags)):
        dt = xs[i] - xs[i-1]
        if dt <= 0: dt = 1e-6
        vs[i] = vs[i-1] + 0.5*(mags[i] + mags[i-1])*dt

    # quitar bias de velocidad
    mean_v = sum(vs)/len(vs)
    vs = [v - mean_v for v in vs]

    # integrar velocidad -> desplazamiento
    ss = [0.0]*len(vs)
    for i in range(1, len(vs)):
        dt = xs[i] - xs[i-1]
        if dt <= 0: dt = 1e-6
        ss[i] = ss[i-1] + 0.5*(vs[i] + vs[i-1])*dt

    # pico-a-pico en µm
    p2p_m = max(ss) - min(ss)
    return abs(p2p_m * 1e6)


def writer_thread_func():
    count = 0
    while not stop_event.is_set():
        try:
            ts, ax, ay, az = row_queue.get(timeout=1.0)
        except queue.Empty:
            continue

        acc_mag = math.sqrt(ax*ax + ay*ay + az*az)
        desp_um = compute_desp_from_acc_window(ts, ax, ay, az)

        estado_lectura = alert_and_log(desp_um, ts)
        estado_lectura = (estado_lectura or "OK").upper()

        ts_iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        payload = {
            "sensor_id": SENSOR_ID,
            "timestamp": ts_iso,
            "ax": ax,
            "ay": ay,
            "az": az,
            "a_total": acc_mag,
            "estado_lectura": estado_lectura,
            "desp_um": desp_um
        }

        count += 1
        if count % 20 == 0:
            safe_insert("lectura", payload)
        if count % 20 == 0:
            print(f"[{datetime.fromtimestamp(ts)}] sample #{count}: desp={desp_um:.2f} µm estado={estado_lectura}")


# ----------------- ARRANQUE -----------------
def start_producer():
    if MODE == 'simulate':
        t = threading.Thread(target=producer_simulate, daemon=True)
    elif MODE == 'serial':
        t = threading.Thread(target=producer_serial, daemon=True)
    else:
        raise ValueError("MODE inválido")
    t.start()
    return t

def main():
    print("Iniciando monitor — MODE =", MODE)
    writer = threading.Thread(target=writer_thread_func, daemon=True)
    writer.start()
    prod = start_producer()
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("Interrumpido por teclado. Deteniendo...")
    finally:
        stop_event.set()
        time.sleep(0.5)
        print("Cerrado.")

if __name__ == "__main__":
    main()
