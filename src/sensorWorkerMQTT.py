import os, time, math, threading, queue, random, json
from datetime import datetime, timezone
from collections import deque, defaultdict
from statistics import pstdev
from dotenv import load_dotenv
from supabase import create_client
from pathlib import Path # <-- 1. Importar Pathlib
import paho.mqtt.client as mqtt # <-- 2. Importar MQTT

try:
    import serial
    from serial.tools import list_ports
except Exception:
    serial = None
    list_ports = None

# $env:SUPABASE_URL="https://vnkbnqdjlgzwnzpsvxdd.supabase.co"
# $env:SUPABASE_KEY="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZua2JucWRqbGd6d256cHN2eGRkIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NTkyNTI3ODYsImV4cCI6MjA3NDgyODc4Nn0.m4e5JB3Yk3Bq5Q3TjnecolfY6pk-r_29JkDjCbxZkoY"

# --- 3. Carga Robusta de.env [15, 18, 20] ---
# Resuelve la ruta absoluta al directorio de este script
BASE_DIR = Path(__file__).resolve().parent
# Carga el archivo.env desde ese directorio
load_dotenv(dotenv_path=BASE_DIR.joinpath('.env'))
# ---------------------------------

# ----------------- CONFIG & THRESHOLDS -----------------
# Elige tu modo desde.env, con "mqtt" como nuevo default
MODE = os.environ.get("MODE", "mqtt") 
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

# --- 4. Configuración MQTT ---
MQTT_BROKER_HOST = os.environ.get("MQTT_BROKER_HOST", "localhost") # "localhost" si usas Docker, o "broker.emqx.io" para pruebas [24]
MQTT_BROKER_PORT = int(os.environ.get("MQTT_BROKER_PORT", "1883"))
DATA_TOPIC_WILDCARD = "dispositivo/+/telemetria" # Tópico Wildcard para datos [25, 26]
COMMAND_TOPIC_PREFIX = "dispositivo"
# -------------------------

# ----------------- Supabase client -----------------
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print(f"Error: No se pudieron cargar las variables de Supabase desde {BASE_DIR.joinpath('.env')}")
    exit(1)

sb = create_client(SUPABASE_URL, SUPABASE_KEY)
print("Cliente de Supabase conectado exitosamente.")

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

mqtt_client = None # Variable global para el cliente MQTT

def _sb_call_with_retry(fn, *args, **kwargs):
    """Wrapper con retry exponencial y jitter para llamadas a Supabase."""
    for attempt in range(1, SB_MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            backoff = SB_BASE_BACKOFF * (2 ** (attempt - 1)) * (0.85 + 0.3 * random.random())
            time.sleep(backoff)
    return None

# ----------------- HELPERS Supabase (Sin cambios) -----------------
def sb_select_sensors():
    """Devuelve lista de sensores {sensor_id, device_serial, enabled} (con retry)."""
    if sb is None: return
    res = _sb_call_with_retry(lambda: sb.table("sensor").select("sensor_id,device_serial,enabled").execute())
    data = getattr(res, "data", None) if res is not None else None
    return data or []

def sb_find_sensor_by_device(device_serial):
    """Devuelve sensor_id (int) o None (con retry)."""
    if sb is None: return None
    res = _sb_call_with_retry(lambda: sb.table("sensor").select("sensor_id").eq("device_serial", device_serial).single().execute())
    data = getattr(res, "data", None) if res is not None else None
    if data and "sensor_id" in data:
        try: return int(data["sensor_id"])
        except Exception: return None
    return None

def sb_get_sensor_enabled(sensor_id):
    """Lee enabled para sensor_id (con retry). Devuelve True/False/None(si error)."""
    if sb is None or sensor_id is None: return False
    res = _sb_call_with_retry(lambda: sb.table("sensor").select("enabled").eq("sensor_id", sensor_id).single().execute())
    if res is None: return None
    data = getattr(res, "data", None)
    if data is None: return False
    return bool(data.get("enabled"))

def safe_insert(table, payload):
    """Intento de insertar con retry. Retorna True si insertó, False si no."""
    if sb is None: return False
    def _do(): return sb.table(table).insert(payload).execute()
    res = _sb_call_with_retry(_do)
    if res is None: return False
    return True

def _insert_event(payload):
    """Inserta un evento en la tabla 'evento'."""
    if sb is None: return None
    res = _sb_call_with_retry(lambda: sb.table("evento").insert(payload).execute())
    if res is None: return None
    data = getattr(res, "data", None)
    if data and isinstance(data, list) and len(data) > 0:
        return data[0].get("evento_id")
    return None

def _update_event(event_id, updates):
    """Actualiza el evento con id = event_id."""
    if sb is None: return False
    res = _sb_call_with_retry(lambda: sb.table("evento").update(updates).eq("evento_id", event_id).execute())
    return res is not None

# ----------------- UTILS (Sin cambios) -----------------
def is_number(s):
    try: float(s); return True
    except: return False

# ----------------- SIMULATE PRODUCER (Sin cambios) -----------------
def producer_simulate_for_sensor(sensor_id, produce_interval=SAMPLE_INTERVAL,
                                 total_disp_target_um=SIMULATE_TARGET_UM, total_time_s=SIMULATE_DURATION_S):
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
        else: bell = 0.0
        ax = random.gauss(0.0, 5e-4); ay = random.gauss(0.0, 5e-4)
        az = g + a_const * bell + random.gauss(0.0, 5e-4); ts = t_now
        item = {"sensor_id": int(sensor_id), "ts": float(ts), "ax": ax, "ay": ay, "az": az}
        try: row_queue.put(item, timeout=0.3)
        except queue.Full: pass
        if total_time_s > 0 and t_rel > total_time_s: start_t = time.time()
        time.sleep(produce_interval)

# ----------------- SERIAL (Sin cambios) -----------------
def try_open_preferred_port(preferred_port=PREFERRED_PORT):
    if serial is None or not preferred_port: return
    if preferred_port in port_to_serial: return
    try:
        ser = serial.Serial(preferred_port, SERIAL_BAUD, timeout=1.0)
        time.sleep(0.05); port_to_serial[preferred_port] = ser
        t = threading.Thread(target=serial_reader_loop, args=(preferred_port, ser), daemon=True)
        port_to_thread[preferred_port] = t; t.start() 
    except Exception as e: print(f" No pude abrir puerto preferido {preferred_port}: {e}")

def scan_and_open_ports():
    if list_ports is None or serial is None: return
    ports = list_ports.comports()
    for p in ports:
        name = p.device
        if name not in port_to_serial:
            try:
                ser = serial.Serial(name, SERIAL_BAUD, timeout=1.0)
                time.sleep(0.05); port_to_serial[name] = ser
                t = threading.Thread(target=serial_reader_loop, args=(name, ser), daemon=True)
                port_to_thread[name] = t; t.start()
            except Exception as e: print(f" No pude abrir puerto {name}: {e}")

def serial_reader_loop(port_name, ser):
    """Lee líneas desde `ser` y encola lecturas con sensor_id asignado."""
    while not stop_event.is_set():
        try:
            raw = ser.readline().decode('utf-8', errors='ignore').strip()
            if not raw: continue
            
            # --- Lógica de parseo híbrida (Serial y MQTT usan JSON) ---
            
            # 1. Mensaje de ID (Solo Serial)
            if raw.startswith("ID,"):
                parts = raw.split(',')
                if len(parts) >= 2:
                    device = parts[1].strip()
                    with mappings_lock:
                        device_to_port[device] = port_name
                        sensor_id = sb_find_sensor_by_device(device)
                        if sensor_id is not None:
                            device_to_sensor[device] = sensor_id
                            sensor_to_device[sensor_id] = device
                            print(f" Mapeado (Serial) device {device} -> sensor_id {sensor_id} (port {port_name})")
                        else:
                            print(f" Device {device} detectado en {port_name} pero SIN sensor configurado en DB")
                continue
            
            # 2. Mensaje de Datos (JSON)
            if raw.startswith("{") and raw.endswith("}"):
                try:
                    data = json.loads(raw)
                    device = data.get("device_id")
                    if not device: continue

                    with mappings_lock:
                        sensor_id = device_to_sensor.get(device)
                    
                    if sensor_id is None:
                        sensor_id = sb_find_sensor_by_device(device)
                        if sensor_id:
                            with mappings_lock:
                                device_to_sensor[device] = sensor_id
                                sensor_to_device[sensor_id] = device
                    
                    if sensor_id:
                        ts_us = float(data['ts_us'])
                        ts = ts_us / 1e6 # Convertir microsegundos a segundos
                        item = {
                            "sensor_id": int(sensor_id), 
                            "ts": ts, 
                            "ax": float(data['ax']), 
                            "ay": float(data['ay']), 
                            "az": float(data['az'])
                        }
                        row_queue.put(item, timeout=0.3)
                    
                except json.JSONDecodeError:
                    print(f" JSON malformado recibido: {raw}")
                except queue.Full:
                    pass # Ignorar
                except Exception as e:
                    print(f" Error procesando JSON: {e}")

        except Exception: time.sleep(0.05)
    try: ser.close()
    except: pass

# --- Lógica MQTT ---
def on_mqtt_connect(client, userdata, flags, rc):
    """Callback de conexión MQTT. Se suscribe a los tópicos de datos."""
    if rc == 0:
        print(f"Conectado al Broker MQTT en {MQTT_BROKER_HOST} [27, 28]")
        client.subscribe(DATA_TOPIC_WILDCARD)
        print(f"Suscrito a tópico wildcard: {DATA_TOPIC_WILDCARD} [25, 26]")
    else:
        print(f"Error de conexión MQTT, código: {rc}")

def on_mqtt_message(client, userdata, msg):
    """Callback de mensaje. Recibe datos de los sensores y los encola."""
    # msg.topic: "dispositivo/sensor_004/telemetria"
    # msg.payload: b'{"device_id": "sensor_004", "ts_us": 12345, "ax": 0.1,...}'
    try:
        # Extraer device_id del tópico [25]
        topic_parts = msg.topic.split('/')
        if len(topic_parts)!= 3 or topic_parts[0]!= "dispositivo" or topic_parts[2]!= "telemetria":
            return 

        device_id = topic_parts[1]
        payload = json.loads(msg.payload.decode('utf-8'))

        with mappings_lock:
            sensor_id = device_to_sensor.get(device_id)
        
        if sensor_id is None:
            sensor_id = sb_find_sensor_by_device(device_id)
            if sensor_id:
                with mappings_lock:
                    device_to_sensor[device_id] = sensor_id
                    sensor_to_device[sensor_id] = device_id
        
        if sensor_id:
            ts_us = float(payload['ts_us'])
            ts = ts_us / 1e6 # Convertir microsegundos a segundos
            ts = time.time()
            item = {
                "sensor_id": int(sensor_id), 
                "ts": ts, 
                "ax": float(payload['ax']), 
                "ay": float(payload['ay']), 
                "az": float(payload['az'])
            }
            row_queue.put(item, timeout=0.3)
        else:
            print(f"Dato MQTT recibido de {device_id}, pero no hay sensor_id mapeado.")

    except queue.Full: pass
    except Exception as e:
        print(f"Error procesando mensaje MQTT: {e} | Tópico: {msg.topic} [29, 30, 31, 32]")

def setup_mqtt_client():
    """Configura e inicia el cliente MQTT."""
    client = mqtt.Client(client_id=f"sensor_worker_backend_{random.randint(0, 1000)}")
    client.on_connect = on_mqtt_connect
    client.on_message = on_mqtt_message # [33, 29, 31, 32, 34]
    try:
        client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, 60)
        client.loop_start() # Inicia hilo en segundo plano NO bloqueante [29, 35, 36, 34, 37]
        return client
    except Exception as e:
        print(f"No se pudo conectar al broker MQTT: {e}")
        return None
# ---------------------

# ----------------- CONTROL THREAD (MODO HÍBRIDO) -----------------
def control_thread_func(poll_interval=CONTROL_POLL_INTERVAL):
    """
    Poll: si enabled True -> Envía CMD,START; si disabled -> Envía CMD,STOP.
    Funciona para TODOS los modos (Serial, MQTT, Simulate).
    """
    global mqtt_client
    print(" Hilo de control iniciado.")
    
    while not stop_event.is_set():
        try:
            sensors = sb_select_sensors() if sb else []
            if not sensors:
                time.sleep(poll_interval)
                continue

            with mappings_lock:
                for s in (sensors or []):
                    try: sid = int(s.get("sensor_id"))
                    except Exception: continue
                    
                    dev = s.get("device_serial")
                    enabled = bool(s.get("enabled"))
                    if dev: sensor_to_device[sid] = dev
                    if not dev: continue # No podemos controlar un sensor sin device_id

                    # Lógica de envío de comando
                    if enabled and sid not in sensor_started:
                        if MODE == "serial":
                            port = device_to_port.get(dev)
                            if port and port in port_to_serial:
                                ser = port_to_serial[port]
                                ser.write(b"CMD,START\n"); ser.flush()
                                sensor_started.add(sid)
                                print(f" Enviado (Serial) 'START' -> sensor {sid} (device {dev} on {port})")
                        
                        if MODE == "mqtt" or MODE == "simulate":
                            if mqtt_client:
                                topic = f"{COMMAND_TOPIC_PREFIX}/{dev}/comando"
                                mqtt_client.publish(topic, "START", qos=1) # [29, 35, 36, 38, 32, 34, 37]
                                sensor_started.add(sid)
                                print(f" Enviado (MQTT) 'START' -> Tópico: {topic}")
                    
                    elif (not enabled) and sid in sensor_started:
                        dev_cached = sensor_to_device.get(sid)
                        if not dev_cached:
                            sensor_started.discard(sid)
                            continue

                        if MODE == "serial":
                            port = device_to_port.get(dev_cached)
                            if port and port in port_to_serial:
                                ser = port_to_serial[port]
                                ser.write(b"CMD,STOP\n"); ser.flush()
                                sensor_started.discard(sid)
                                print(f" Enviado (Serial) 'STOP' -> sensor {sid} (device {dev_cached} on {port})")
                            else:
                                sensor_started.discard(sid) # Descartar si el puerto ya no existe
                        
                        if MODE == "mqtt" or MODE == "simulate":
                            if mqtt_client:
                                topic = f"{COMMAND_TOPIC_PREFIX}/{dev_cached}/comando"
                                mqtt_client.publish(topic, "STOP", qos=1) # [29, 35, 36, 38, 32, 34, 37]
                                sensor_started.discard(sid)
                                print(f" Enviado (MQTT) 'STOP' -> Tópico: {topic}")

        except Exception as e:
            print(f" Error en el bucle de control: {e}")
        
        time.sleep(poll_interval)
    print(" terminado")


# ----------------- ALERTS / CÁLCULOS (Sin cambios) -----------------
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

# ----------------- WRITER (Sin cambios) -----------------
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

# ----------------- ORQUESTACIÓN / START (MODO HÍBRIDO) -----------------
def start_serial_scanner_loop():
    while not stop_event.is_set():
        scan_and_open_ports()
        time.sleep(SERIAL_SCAN_INTERVAL)

def main():
    global mqtt_client
    print(f"Iniciando monitor_multi — MODE = {MODE}")
    writer = threading.Thread(target=writer_thread_func, daemon=True)
    writer.start()

    control_thread = None # Hilo de control
    
    if MODE == "mqtt":
        mqtt_client = setup_mqtt_client()
        if mqtt_client is None:
            print("Error fatal: No se pudo iniciar el cliente MQTT. Saliendo.")
            return
        if sb:
            control_thread = threading.Thread(target=control_thread_func, daemon=True)
            control_thread.start()

    elif MODE == "serial":
        try_open_preferred_port(PREFERRED_PORT)
        if list_ports is None or serial is None:
            print("pyserial no instalado o no disponible. Instala pyserial si querés modo serial.")
            return
        scanner = threading.Thread(target=start_serial_scanner_loop, daemon=True)
        scanner.start()
        if sb:
            control_thread = threading.Thread(target=control_thread_func, daemon=True)
            control_thread.start()
            
    elif MODE == 'simulate':
        # simulate: arrancar un producer por cada sensor en DB
        mqtt_client = setup_mqtt_client() # Modo simulación también usa MQTT para control
        if mqtt_client is None:
            print("Error: Modo simulación requiere MQTT para control. Saliendo.")
            return

        if sb:
            sensors = sb_select_sensors()
            started = set()
            for s in sensors:
                try: sid = int(s.get("sensor_id"))
                except Exception: continue
                if sid in started: continue
                t = threading.Thread(target=producer_simulate_for_sensor, args=(sid, SAMPLE_INTERVAL, SIMULATE_TARGET_UM, SIMULATE_DURATION_S), daemon=True)
                t.start(); started.add(sid)
                print(f" Producer iniciado para sensor {sid}")
            
            control_thread = threading.Thread(target=control_thread_func, daemon=True)
            control_thread.start()
        else:   
            print(" supabase no disponible.")
    else:
        raise ValueError("MODE inválido. Elige 'serial', 'mqtt' o 'simulate'")
        
    try:
        while True:
            time.sleep(0.05)
    except KeyboardInterrupt:
        print("Interrumpido por teclado. Deteniendo...")
    finally:
        stop_event.set()
        if mqtt_client:
            mqtt_client.loop_stop() # [29, 36]
            mqtt_client.disconnect()
            print("Cliente MQTT desconectado.")
        
        with mappings_lock:
            for p, s in list(port_to_serial.items()):
                try: s.close()
                except: pass
        
        if control_thread:
            control_thread.join(timeout=1.0)
            
        time.sleep(0.05)
        print("Cerrado.")

if __name__ == "__main__":
    main()