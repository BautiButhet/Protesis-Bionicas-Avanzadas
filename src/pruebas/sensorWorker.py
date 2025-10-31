import os, time, math, threading, queue, random
from datetime import datetime, timezone
from collections import deque, defaultdict
from statistics import pstdev
from supabase import create_client
try:
    import serial
    from serial.tools import list_ports
except Exception:
    serial = None
    list_ports = None

from dotenv import load_dotenv
load_dotenv()

# ----------------- CONFIG & THRESHOLDS -----------------
MODE = os.environ.get("MODE", "serial")
PREFERRED_PORT = os.environ.get("SERIAL_PORT", "COM3")
SERIAL_BAUD = int(os.environ.get("SERIAL_BAUD", "115200"))
CONTROL_POLL_INTERVAL = float(os.environ.get("CONTROL_POLL_INTERVAL", "1.0"))
SERIAL_SCAN_INTERVAL = float(os.environ.get("SERIAL_SCAN_INTERVAL", "1.0"))
SAMPLE_INTERVAL = float(os.environ.get("SAMPLE_INTERVAL", "0.05"))
SIMULATE_TARGET_UM = float(os.environ.get("SIMULATE_TARGET_UM", "150.0"))
SIMULATE_DURATION_S = float(os.environ.get("SIMULATE_DURATION_S", "10.0"))
THRESH_OK_UM = float(os.environ.get("THRESH_OK_UM", "50.0"))
THRESH_ALERT_UM = float(os.environ.get("THRESH_ALERT_UM", "150.0"))
ACC_WINDOW_SECONDS = float(os.environ.get("ACC_WINDOW_SECONDS", "2.0"))

# ----------------- Supabase client -----------------
# SUPABASE_URL = os.environ.get("SUPABASE_URL")
# SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

sb = create_client(SUPABASE_URL, SUPABASE_KEY)

# ----------------- QUEUES / FLAGS -----------------
row_queue = queue.Queue(maxsize=30000)   # items: dict {sensor_id, ts, ax, ay, az}
stop_event = threading.Event()

SAMPLES_PER_INSERT = int(round(1.0 / SAMPLE_INTERVAL)) if SAMPLE_INTERVAL > 0 else 20
buffers_by_sensor = defaultdict(lambda: deque(maxlen=SAMPLES_PER_INSERT))
acc_window = deque()

# ----------------- SERIAL / MAPPING STATE -----------------
port_to_serial = {}           # port_name -> serial.Serial object
port_to_thread = {}           # port_name -> thread object
device_to_port = {}           # device_id -> port_name
device_to_sensor = {}         # device_id -> sensor_id (cache)
sensor_to_device = {}         # sensor_id -> device_id (cache)
sensor_started = set()        # sensor_id que estan START localmente
mappings_lock = threading.Lock()

pre_mapping_buffer = defaultdict(list)
PREMAP_MAX = int(os.environ.get("PREMAP_MAX", "200"))

# ----------------- Supabase retry params -----------------
SB_MAX_RETRIES = int(os.environ.get("SB_MAX_RETRIES", "4"))
SB_BASE_BACKOFF = float(os.environ.get("SB_BASE_BACKOFF", "0.2"))

def _sb_call_with_retry(fn, *args, **kwargs):
    """Wrapper con retry exponencial y jitter para llamadas a Supabase.
    Por defecto NO imprime cada intento fallido (silenciado).
    """
    for attempt in range(1, SB_MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            backoff = SB_BASE_BACKOFF * (2 ** (attempt - 1)) * (0.85 + 0.3 * random.random())

            time.sleep(backoff)
    return None

# ----------------- HELPERS Supabase (mantener tu estilo) -----------------
def sb_select_sensors():
    """Devuelve lista de sensores {sensor_id, device_serial, enabled} (con retry)."""
    if sb is None:
        return []
    res = _sb_call_with_retry(lambda: sb.table("sensor").select("sensor_id,device_serial,enabled").execute())
    data = getattr(res, "data", None) if res is not None else None
    return data or []

def sb_find_sensor_by_device(device_serial):
    """Devuelve sensor_id (int) o None (con retry)."""
    if sb is None:
        return None
    res = _sb_call_with_retry(lambda: sb.table("sensor").select("sensor_id").eq("device_serial", device_serial).single().execute())
    data = getattr(res, "data", None) if res is not None else None
    if data and "sensor_id" in data:
        try:
            return int(data["sensor_id"])
        except Exception:
            return None
    return None

def sb_get_sensor_enabled(sensor_id):
    """Lee enabled para sensor_id (con retry). Devuelve True/False/None(si error)."""
    if sb is None or sensor_id is None:
        return False
    res = _sb_call_with_retry(lambda: sb.table("sensor").select("enabled").eq("sensor_id", sensor_id).single().execute())
    if res is None:
        return None
    data = getattr(res, "data", None)
    if data is None:
        return False
    return bool(data.get("enabled"))

def safe_insert(table, payload):
    """Intento de insertar con retry. Retorna True si insertó, False si no."""
    if sb is None:
        return False
    def _do():
        return sb.table(table).insert(payload).execute()
    res = _sb_call_with_retry(_do)
    if res is None:
        return False
    return True

def _insert_event(payload):
    """
    Inserta un evento en la tabla 'evento'.
    Devuelve el id insertado (int) si tuvo éxito, o None si falló.
    """
    if sb is None:
        return None
    res = _sb_call_with_retry(lambda: sb.table("evento").insert(payload).execute())
    if res is None:
        return None
    data = getattr(res, "data", None)
    if data and isinstance(data, list) and len(data) > 0:
        return data[0].get("evento_id")
    return None

def _update_event(event_id, updates):
    """
    Actualiza el evento con id = event_id.
    """
    if sb is None:
        return False
    res = _sb_call_with_retry(lambda: sb.table("evento").update(updates).eq("evento_id", event_id).execute())
    return res is not None

# ----------------- UTILS -----------------
def is_number(s):
    try:
        float(s)
        return True
    except:
        return False

# ----------------- SIMULATE PRODUCER (por sensor) -----------------
def producer_simulate_for_sensor(sensor_id, produce_interval=SAMPLE_INTERVAL,
                                 total_disp_target_um=SIMULATE_TARGET_UM, total_time_s=SIMULATE_DURATION_S):
    """Producer simulate que sólo produce si sensor.enabled==True."""
    g = 9.81
    x_m = total_disp_target_um * 1e-6
    a_const = 2.0 * x_m / (total_time_s * total_time_s) if total_time_s > 0 else 0.0
    start_t = time.time()
    while not stop_event.is_set():
        enabled = sb_get_sensor_enabled(sensor_id) if sb else True
        if not enabled:
            time.sleep(0.1)
            start_t = time.time()
            continue

        t_now = time.time()
        t_rel = t_now - start_t
        if total_time_s > 0:
            mu = total_time_s / 2.0
            sigma = total_time_s / 6.0
            bell = math.exp(-0.5 * ((t_rel - mu) / sigma) ** 2)
        else:
            bell = 0.0

        ax = random.gauss(0.0, 5e-4)
        ay = random.gauss(0.0, 5e-4)
        az = g + a_const * bell + random.gauss(0.0, 5e-4)
        ts = t_now

        item = {"sensor_id": int(sensor_id), "ts": float(ts), "ax": ax, "ay": ay, "az": az}
        try:
            row_queue.put(item, timeout=0.3)
        except queue.Full:
            pass

        if total_time_s > 0 and t_rel > total_time_s:
            start_t = time.time()
        time.sleep(produce_interval)

# ----------------- SERIAL: open preferred, scan, reader -----------------
def try_open_preferred_port(preferred_port=PREFERRED_PORT):
    """Intenta abrir puerto preferido al inicio (COM3 por defecto)."""
    if serial is None or not preferred_port:
        return
    if preferred_port in port_to_serial:
        return
    try:
        ser = serial.Serial(preferred_port, SERIAL_BAUD, timeout=1.0)
        time.sleep(0.05)
        port_to_serial[preferred_port] = ser
        t = threading.Thread(target=serial_reader_loop, args=(preferred_port, ser), daemon=True)
        port_to_thread[preferred_port] = t
        t.start() 
    except Exception as e:
        print(f"[PREFERRED] No pude abrir puerto preferido {preferred_port}: {e}")

def scan_and_open_ports():
    """Escanea puertos y abre nuevos."""
    if list_ports is None or serial is None:
        return
    ports = list_ports.comports()
    for p in ports:
        name = p.device
        if name not in port_to_serial:
            try:
                ser = serial.Serial(name, SERIAL_BAUD, timeout=1.0)
                time.sleep(0.05)
                port_to_serial[name] = ser
                t = threading.Thread(target=serial_reader_loop, args=(name, ser), daemon=True)
                port_to_thread[name] = t
                t.start()
            except Exception as e:
                print(f"[SCAN] No pude abrir puerto {name}: {e}")

def serial_reader_loop(port_name, ser):
    """Lee líneas desde `ser` y encola lecturas con sensor_id asignado."""
    while not stop_event.is_set():
        try:
            raw = ser.readline().decode('utf-8', errors='ignore').strip()
            if not raw:
                continue
            parts = raw.split(',')

            # ID message
            if parts[0].upper() == "ID" and len(parts) >= 2:
                device = parts[1].strip()
                with mappings_lock:
                    device_to_port[device] = port_name
                    sensor_id = sb_find_sensor_by_device(device)
                    if sensor_id is not None:
                        device_to_sensor[device] = sensor_id
                        sensor_to_device[sensor_id] = device
                        print(f"[READER] mapeado device {device} -> sensor_id {sensor_id} (port {port_name})")
                    else:
                        print(f"[READER] device {device} detectado en {port_name} pero SIN sensor configurado en DB")
                    # procesar buffer pre-mapping
                    if pre_mapping_buffer.get(device):
                        for raw_line in pre_mapping_buffer[device]:
                            try:
                                p2 = raw_line.split(',')
                                if len(p2) >= 5 and (not is_number(p2[0])):
                                    ts_us = float(p2[1])
                                    ts = ts_us / 1e6
                                    ax = float(p2[2])
                                    ay = float(p2[3])
                                    az = float(p2[4])
                                    sid = device_to_sensor.get(device)
                                    if sid is not None:
                                        row_queue.put({"sensor_id": int(sid), "ts": float(ts), "ax": ax, "ay": ay, "az": az}, timeout=0.3)
                            except Exception:
                                pass
                        pre_mapping_buffer[device].clear()
                continue

            ts = None; ax = ay = az = None; sensor_id = None

            # Forma A: DEVICE_ID,ts_us,ax,ay,az
            if len(parts) >= 5 and (not is_number(parts[0])):
                device = parts[0].strip()
                try:
                    ts_us = float(parts[1])
                    ts = ts_us / 1e6
                    ax = float(parts[2])
                    ay = float(parts[3])
                    az = float(parts[4])
                except Exception:
                    continue
                with mappings_lock:
                    sensor_id = device_to_sensor.get(device)
                if sensor_id is None:
                    sensor_id = sb_find_sensor_by_device(device)
                    if sensor_id is not None:
                        with mappings_lock:
                            device_to_sensor[device] = sensor_id
                            sensor_to_device[sensor_id] = device
                if sensor_id is None:
                    with mappings_lock:
                        if len(pre_mapping_buffer[device]) < PREMAP_MAX:
                            pre_mapping_buffer[device].append(raw)
                    continue

            # Forma B: ts_us,ax,ay,az -> usar mapping port->device
            elif len(parts) >= 4:
                try:
                    ts_us = float(parts[0])
                    ts = ts_us / 1e6
                    ax = float(parts[1])
                    ay = float(parts[2])
                    az = float(parts[3])
                except Exception:
                    continue
                with mappings_lock:
                    device = None
                    for d, p in device_to_port.items():
                        if p == port_name:
                            device = d
                            break
                    if device:
                        sensor_id = device_to_sensor.get(device)
                    else:
                        sensor_id = None
                if sensor_id is None:
                    continue
            else:
                continue

            if sensor_id is not None and ts is not None:
                item = {"sensor_id": int(sensor_id), "ts": float(ts), "ax": float(ax), "ay": float(ay), "az": float(az)}
                try:
                    row_queue.put(item, timeout=0.3)
                except queue.Full:
                    pass

        except Exception:
            time.sleep(0.05)

    try:
        ser.close()
    except:
        pass

# ----------------- CONTROL THREAD -----------------
def control_thread_func(poll_interval=CONTROL_POLL_INTERVAL):
    """Poll: si enabled True y hay mapping -> CMD,START; si disabled -> CMD,STOP."""
    while not stop_event.is_set():
        sensors = sb_select_sensors() if sb else []
        with mappings_lock:
            for s in (sensors or []):
                try:
                    sid = int(s.get("sensor_id"))
                except Exception:
                    continue
                dev = s.get("device_serial")
                enabled = bool(s.get("enabled"))
                if dev:
                    sensor_to_device[sid] = dev

                if enabled and sid not in sensor_started:
                    port = device_to_port.get(dev) if dev else None
                    if port and port in port_to_serial:
                        try:
                            ser = port_to_serial[port]
                            ser.write(b"CMD,START\n"); ser.flush()
                            sensor_started.add(sid)
                            print(f"[CONTROL] Enviado CMD,START sensor {sid} -> device {dev} on {port}")
                        except Exception as e:
                            print("[CONTROL] Error enviando START:", e)
                elif (not enabled) and sid in sensor_started:
                    dev_cached = sensor_to_device.get(sid)
                    port = device_to_port.get(dev_cached) if dev_cached else None
                    if port and port in port_to_serial:
                        try:
                            ser = port_to_serial[port]
                            ser.write(b"CMD,STOP\n"); ser.flush()
                            sensor_started.discard(sid)
                            print(f"[CONTROL] Enviado CMD,STOP sensor {sid} -> device {dev_cached} on {port}")
                        except Exception as e:
                            print("[CONTROL] Error enviando STOP:", e)
                    else:
                        sensor_started.discard(sid)
        time.sleep(poll_interval)
    print("[CONTROL] terminado")

# ----------------- ALERTS / CÁLCULOS (per-sensor) -----------------
event_state = {"state_by_sensor": {}, "event_id_by_sensor": {}, "warning_since_by_sensor": {}}
event_lock = threading.Lock()

def alert_and_log_per_sensor(desp_um, ts_s, sensor_id):
    with event_lock:
        st = event_state["state_by_sensor"].get(sensor_id, "OK")
        warning_since = event_state["warning_since_by_sensor"].get(sensor_id)
        current_event_id = event_state["event_id_by_sensor"].get(sensor_id)

        ts_iso = datetime.fromtimestamp(ts_s, tz=timezone.utc).isoformat()
        if desp_um > THRESH_ALERT_UM:
            if st != "ALERT":
                if current_event_id is not None and warning_since:
                    _update_event(current_event_id, {"event_duration": ts_s - warning_since})
                    event_state["event_id_by_sensor"][sensor_id] = None
                st = "ALERT"
                warning_since = ts_s
                payload = {
                    "sensor_id": sensor_id, 
                    "timestamp": ts_iso, 
                    "desp_um": desp_um, 
                    "estado_lectura":"ALERT", 
                    "message": f"ALERTA sensor {sensor_id}"}
                eid = _insert_event(payload)
                event_state["event_id_by_sensor"][sensor_id] = eid
            event_state["state_by_sensor"][sensor_id] = st
            event_state["warning_since_by_sensor"][sensor_id] = warning_since
            return "ALERT"

        if desp_um > THRESH_OK_UM:
            if st != "WARNING":
                if current_event_id is not None and warning_since:
                    _update_event(current_event_id, {"event_duration": ts_s - warning_since})
                    event_state["event_id_by_sensor"][sensor_id] = None
                st = "WARNING"
                warning_since = ts_s
                payload = {
                    "sensor_id": sensor_id, 
                    "timestamp": ts_iso, 
                    "desp_um": desp_um, 
                    "estado_lectura":"WARNING", 
                    "message": f"WARNING sensor {sensor_id}"}
                eid = _insert_event(payload)
                event_state["event_id_by_sensor"][sensor_id] = eid
            event_state["state_by_sensor"][sensor_id] = st
            event_state["warning_since_by_sensor"][sensor_id] = warning_since
            return "WARNING"

        if st != "OK":
            if current_event_id is not None and warning_since:
                _update_event(current_event_id, {"event_duration": ts_s - warning_since})
            st = "OK"
            warning_since = None
            event_state["state_by_sensor"][sensor_id] = st
            event_state["warning_since_by_sensor"][sensor_id] = warning_since
            event_state["event_id_by_sensor"][sensor_id] = None
            return "OK"
        return st

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

# ----------------- WRITER -----------------
def writer_thread_func():
    """Consume row_queue con items por sensor y hace insert por sensor (promediando)."""
    count = 0
    print("[WRITER] iniciado")
    while not stop_event.is_set():
        try:
            item = row_queue.get(timeout=1.0)
        except queue.Empty:
            continue

        sensor_id = int(item.get("sensor_id"))
        ts = float(item.get("ts"))
        ax = float(item.get("ax"))
        ay = float(item.get("ay"))
        az = float(item.get("az"))

        # cálculo por sensor
        desp_um = compute_desp_from_acc_window(ts, ax, ay, az)

        buffers_by_sensor[sensor_id].append({
            "ts": ts,
            "ax": ax,
            "ay": ay,
            "az": az,
            "a_total": math.sqrt(ax*ax + ay*ay + az*az),
            "desp_um": desp_um
        })

        count += 1

        if len(buffers_by_sensor[sensor_id]) >= SAMPLES_PER_INSERT:
            buf = buffers_by_sensor[sensor_id]
            n = len(buf)
            avg_ts = sum(x["ts"] for x in buf) / n
            avg_ax = sum(x["ax"] for x in buf) / n
            avg_ay = sum(x["ay"] for x in buf) / n
            avg_az = sum(x["az"] for x in buf) / n
            avg_atot = sum(x["a_total"] for x in buf) / n
            avg_desp = sum(x["desp_um"] for x in buf) / n

            # desviación de las muestras de desplazamiento (informativa)
            try:
                desp_std = pstdev([x["desp_um"] for x in buf])
            except Exception:
                desp_std = 0.0

            # Generar estado basado en el valor promedio de desplazamiento
            estado = alert_and_log_per_sensor(avg_desp, avg_ts, sensor_id)
            estado = (estado or "OK").upper()

            ts_iso = datetime.fromtimestamp(avg_ts, tz=timezone.utc).isoformat()

            payload = {
                "sensor_id": sensor_id,
                "timestamp": ts_iso,
                "ax": avg_ax, 
                "ay": avg_ay, 
                "az": avg_az,
                "a_total": avg_atot,
                "estado_lectura": estado,
                "desp_um": avg_desp,
                "desp_std": desp_std
            }

            # insertar promedio en Supabase
            ok = safe_insert("lectura", payload)
            if ok:
                print(f"[{datetime.fromtimestamp(avg_ts)}] INSERT promedio #{count//SAMPLES_PER_INSERT}: "
                      f"desp_avg={avg_desp:.2f} µm std={desp_std:.2f} µm estado={estado}")
            else:
                print(f"[{datetime.fromtimestamp(avg_ts)}] ERROR INSERT promedio disp={avg_desp:.2f} µm estado={estado}")
            
            # vaciar buffer para la próxima ventana (no solapado)
            buffers_by_sensor[sensor_id].clear()

# ----------------- ORQUESTACIÓN / START -----------------
def start_serial_scanner_loop():
    while not stop_event.is_set():
        scan_and_open_ports()
        time.sleep(SERIAL_SCAN_INTERVAL)

def main():
    print("Iniciando monitor_multi — MODE =", MODE)
    writer = threading.Thread(target=writer_thread_func, daemon=True)
    writer.start()

    if MODE == "serial":
        try_open_preferred_port(PREFERRED_PORT)
        if list_ports is None or serial is None:
            print("pyserial no instalado o no disponible. Instala pyserial si querés modo serial.")
            return
        scanner = threading.Thread(target=start_serial_scanner_loop, daemon=True)
        scanner.start()
        if sb:
            control = threading.Thread(target=control_thread_func, daemon=True)
            control.start()
    elif MODE == 'simulate':
        # simulate: arrancar un producer por cada sensor en DB
        if sb:
            sensors = sb_select_sensors()
            started = set()
            for s in sensors:
                try:
                    sid = int(s.get("sensor_id"))
                except Exception:
                    continue
                if sid in started:
                    continue
                t = threading.Thread(target=producer_simulate_for_sensor, args=(sid, SAMPLE_INTERVAL, SIMULATE_TARGET_UM, SIMULATE_DURATION_S), daemon=True)
                t.start()
                started.add(sid)
                print(f"[SIM] Producer iniciado para sensor {sid}")
        else:   
            print("[SIM] supabase no disponible.")
        if sb:
            control = threading.Thread(target=control_thread_func, daemon=True)
            control.start()
    else:
        raise ValueError("MODE inválido")
    try:
        while True:
            time.sleep(0.05)
    except KeyboardInterrupt:
        print("Interrumpido por teclado. Deteniendo...")
    finally:
        stop_event.set()
        with mappings_lock:
            for p, s in list(port_to_serial.items()):
                try:
                    s.close()
                except:
                    pass
        time.sleep(0.05)
        print("Cerrado.")

if __name__ == "__main__":
    main()