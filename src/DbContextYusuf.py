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
from statistics import pstdev

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
SAMPLE_INTERVAL = 0.05   # s -> 20 Hz en simulate / 20 muestras por segundo

# umbrales (µm)
THRESH_OK_UM = 50.0
THRESH_ALERT_UM = 150.0

# ----------------- QUEUES / FLAGS -----------------
row_queue = queue.Queue(maxsize=20000)
stop_event = threading.Event()

SAMPLES_PER_INSERT = int(round(1.0 / SAMPLE_INTERVAL)) if SAMPLE_INTERVAL > 0 else 20
# buffer para promediar las N muestras antes de insertar
samples_buffer = deque(maxlen=SAMPLES_PER_INSERT)

# state
current_state = "OK"
warning_since = None
state_lock = threading.Lock()
current_event_id = None

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
            ts_us = float(parts[0]); 
            ts = ts_us / 1e6
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
    """
    Inserta un evento en la tabla 'evento'.
    Devuelve el id insertado (int) si tuvo éxito, o None si falló.
    """
    try:
        # pedimos que retorne el id del registro insertado
        res = sb.table("evento").insert(payload).execute()
        # res.data normalmente es una lista de dicts con el registro insertado
        data = getattr(res, "data", None)
        if data and isinstance(data, list) and len(data) > 0:
            row = data[0]
        # intenta varios nombres posibles (ajusta si tu PK tiene otro nombre)
            eid = row.get("evento_id")
            if eid is not None:
                return eid
            return None 
    except Exception as e:
        print("Error inserting event to Supabase:", e)
        return None

def _update_event(event_id, updates):
    """
    Actualiza el evento con id = event_id.
    Devuelve True si actualizó, False si falló.
    """
    try:
        # actualizamos la fila con la PK event_id
        res = sb.table("evento").update(updates).eq("evento_id", event_id).execute()
        return True
    except Exception as e:
        print("Error updating event into Supabase:", e)
    return False     

def alert_and_log(desp_um, ts_s):
    """
    Actualiza el estado global y crea/actualiza eventos en la tabla 'eventos'.
    - Cuando pasa a WARNING o ALERT se inserta un evento y se guarda su id en current_event_id.
    - Cuando vuelve a OK se intenta actualizar el evento correspondiente.
    """
    global current_state, warning_since, current_event_id
    with state_lock:
        ts_iso = datetime.fromtimestamp(ts_s, tz=timezone.utc).isoformat()
        if desp_um > THRESH_ALERT_UM:
            if current_state != "ALERT":
                duration_s = None
                if warning_since:
                    duration_s = ts_s - warning_since
                if current_event_id is not None:
                    updates = {
                        "event_duration": duration_s if duration_s is not None else 0.0,
                    }
                    ok = _update_event(current_event_id, updates)
                    current_event_id = None
                
                current_state = "ALERT"
                warning_since = ts_s
                msg = f"ALERTA INMEDIATA: desp {desp_um:.1f} µm > {THRESH_ALERT_UM} µm"
                payload = {
                    "sensor_id": SENSOR_ID,
                    "timestamp": ts_iso,
                    "desp_um": desp_um,
                    "estado_lectura": "ALERT",
                    "message": msg
                }
                eid = _insert_event(payload)
                if eid:
                    current_event_id = eid
                else:
                    current_event_id = None
                print(f"[{datetime.fromtimestamp(ts_s)}] {msg}")
            return "ALERT"

        if desp_um > THRESH_OK_UM:
            if current_state != "WARNING":
                duration_s = None
                if warning_since:
                    duration_s = ts_s - warning_since
                if current_event_id is not None:
                    updates = {
                        "event_duration": duration_s if duration_s is not None else 0.0,
                    }
                    ok = _update_event(current_event_id, updates)
                    current_event_id = None
                
                current_state = "WARNING"
                warning_since = ts_s
                msg = f"ADVERTENCIA: desp {desp_um:.1f} µm > {THRESH_OK_UM} µm"
                payload = {
                    "sensor_id": SENSOR_ID,
                    "timestamp": ts_iso,
                    "desp_um": desp_um,
                    "estado_lectura": "WARNING",
                    "message": msg
                }
                eid = _insert_event(payload)
                if eid:
                    current_event_id = eid
                else:
                    current_event_id = None
                print(f"[{datetime.fromtimestamp(ts_s)}] {msg}")
            return "WARNING"

        if current_state != "OK":
            duration_s = None
            if warning_since:
                duration_s = ts_s - warning_since
            msg = f"VOLVIO A OK"
            print(f"[{datetime.fromtimestamp(ts_s)}] {msg}")

            if current_event_id is not None:
                updates = {
                    "event_duration": duration_s if duration_s is not None else 0.0,
                }
                ok = _update_event(current_event_id, updates)
                current_event_id = None

            warning_since = None
            current_state = "OK"
            return "OK"
        # estado no cambiado
        return current_state

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
    """
    Consume muestras individuales (ts, ax, ay, az) del row_queue,
    calcula desp_um por ventana y guarda únicamente el promedio de
    SAMPLES_PER_INSERT muestras en la tabla 'lectura'.
    """
    count = 0
    while not stop_event.is_set():
        try:
            ts, ax, ay, az = row_queue.get(timeout=1.0)
        except queue.Empty:
            continue

        # cálculo por muestra
        acc_mag = math.sqrt(ax*ax + ay*ay + az*az)
        desp_um = compute_desp_from_acc_window(ts, ax, ay, az)

        # apilar la muestra en el buffer (guardamos ts como float)
        samples_buffer.append({
            "ts": float(ts),     # <-- mantener float para poder promediar
            "ax": ax,
            "ay": ay,
            "az": az,
            "a_total": acc_mag,
            "desp_um": desp_um
        })

        count += 1

        # cada SAMPLES_PER_INSERT muestras calculamos el promedio y lo guardamos
        if len(samples_buffer) >= SAMPLES_PER_INSERT:
            n = len(samples_buffer)
            # sumarizar/normalizar (usamos math.fsum para más precisión si querés)
            sum_ts = sum(item["ts"] for item in samples_buffer)
            sum_ax = sum(item["ax"] for item in samples_buffer)
            sum_ay = sum(item["ay"] for item in samples_buffer)
            sum_az = sum(item["az"] for item in samples_buffer)
            sum_atot = sum(item["a_total"] for item in samples_buffer)
            sum_desp = sum(item["desp_um"] for item in samples_buffer)

            avg_ts = sum_ts / n
            avg_ax = sum_ax / n
            avg_ay = sum_ay / n
            avg_az = sum_az / n
            avg_atot = sum_atot / n
            avg_desp = sum_desp / n

            # desviación de las muestras de desplazamiento (informativa)
            try:
                desp_std = pstdev([item["desp_um"] for item in samples_buffer])
            except Exception:
                desp_std = 0.0

            # Generar estado basado en el valor promedio de desplazamiento
            estado_lectura = alert_and_log(avg_desp, avg_ts)
            estado_lectura = (estado_lectura or "OK").upper()

            # timestamp ISO para la insert
            ts_iso = datetime.fromtimestamp(avg_ts, tz=timezone.utc).isoformat()

            payload = {
                "sensor_id": SENSOR_ID,
                "timestamp": ts_iso,
                "ax": avg_ax,
                "ay": avg_ay,
                "az": avg_az,
                "a_total": avg_atot,
                "estado_lectura": estado_lectura,
                "desp_um": avg_desp,
                "desp_std": desp_std,
            }

            # insertar promedio en Supabase
            ok = safe_insert("lectura", payload)
            if ok:
                print(f"[{datetime.fromtimestamp(avg_ts)}] INSERT promedio #{count//SAMPLES_PER_INSERT}: "
                      f"desp_avg={avg_desp:.2f} µm std={desp_std:.2f} µm estado={estado_lectura}")
            else:
                print(f"[{datetime.fromtimestamp(avg_ts)}] ERROR INSERT promedio disp={avg_desp:.2f} µm estado={estado_lectura}")

            # vaciar buffer para la próxima ventana (no solapado)
            samples_buffer.clear()
            
# --------------- ARRANQUE -----------------
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
