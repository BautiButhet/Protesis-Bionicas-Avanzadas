# Monitor de micro-desplazamiento para prótesis (versión simulador/serial).
#  - MODOS: 'simulate' | 'serial' (el primero simula datos y el segundo toma datos reales desde el microcontrolador)
#  - Guarda en un CSV (main.csv) y en una base DB SQLite (main.db) con tablas Mediciones y Eventos (TODAVIA NO DESARROLLADO)
#  - Archivo events.csv con registro de eventos (alarma y warning)
#  - Hace una simulación progresiva controlada con fase 1 (lenta y en rango permitido) y fase 2 (puede pasar 150 µm)

import os, csv, time, math, threading, queue, random, sqlite3
from collections import deque
from datetime import datetime
try:
    import serial
except Exception:
    serial = None
import matplotlib.pyplot as plt
import matplotlib.animation as animation

# --------------- CONFIG de variables GLOBALES ----------------
MODE = 'simulate'         # 'simulate' (simulador de data) o 'serial' (datos reales desde el sensor)
SERIAL_PORT = 'COM3'
BAUD_RATE = 115200

SESSION_FOLDER = '.'
MASTER_CSV = os.path.join(SESSION_FOLDER, 'main.csv')
SQLITE_DB = os.path.join(SESSION_FOLDER, 'main.db')
EVENTS_CSV = os.path.join(SESSION_FOLDER, 'events.csv')

HISTORY_SECONDS = 30.0
MAX_POINTS = 2000
WRITE_FLUSH_INTERVAL = 1.0

# Parámetros físicos / cálculo
SENSOR_RADIUS_MM = 30.0      # distancia sensor -> centro rotación (mm). AJUSTAR según montaje
ACC_WINDOW_SECONDS = 1.0

# Umbrales (µm)
THRESH_OK_UM = 50
THRESH_WARNING_UM = 100.0
THRESH_ALERT_UM = 150.0
SUSTAIN_SECONDS_MIN = 2.5 #tiempo q debe mantenerse por encima de thresh_ok para considerar warning
SUSTAIN_SECONDS_MAX = 10 #tiempo q debe mantenerse por encima de thresh_ok para considerar volver a avisar

# Simulacion: fases y suavizado
SIM_MIN_UM = 5
SIM_PHASE1_MAX_UM = 100 
SIM_PHASE2_MAX_UM = 200.0
SIM_MEAN_UM = 40.0
SIM_PRODUCE_INTERVAL = 0.05     # s (20 Hz)
SIM_PHASE1_SECONDS = 15.0       # fase1 duracion
SIM_OU_THETA = 0.6
SIM_OU_SIGMA = 0.8
SIM_MAX_STEP_UM = 5.0
SIM_MAX_OMEGA_DEG = 400.0

# CSV header (mediciones + columnas de desplazamiento calculadas/registradas)
HEADER = ['ts_s','ax','ay','az','gx','gy','gz','acc_mag','gyro_mag','disp_um_gyro','disp_um_acc','disp_um_combined']

# ----------------- estructuras -----------------------
row_queue = queue.Queue(maxsize=20000)
plot_lock = threading.Lock()
times = deque(maxlen=MAX_POINTS)
acc_mag = deque(maxlen=MAX_POINTS)
gyro_mag = deque(maxlen=MAX_POINTS)
disp_um_buf = deque(maxlen=MAX_POINTS)

stop_event = threading.Event()
csv_file = None; csv_writer = None

# ventana para integración por acelerómetro
acc_window = deque()
last_ts_for_gyro = None

# estado alarmas
current_state = "OK"
warning_since = None
start_time = time.time()

# ----------------- SQLite init ----------------------
def init_db(path):
    conn = sqlite3.connect(path, check_same_thread=False)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS measurements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_s REAL,
            ax REAL, ay REAL, az REAL,
            gx REAL, gy REAL, gz REAL,
            acc_mag REAL, gyro_mag REAL,
            disp_um_gyro REAL, disp_um_acc REAL, disp_um_combined REAL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_s REAL,
            disp_um REAL,
            level TEXT,
            message TEXT
        )
    """)
    conn.commit()
    return conn

# ---------------- PRODUCER: SIMULATE --------------
def producer_simulate(produce_interval=SIM_PRODUCE_INTERVAL):
    """
    Producer simulate:
     - genera x: desplazamiento absoluto objetivo (µm), suave y controlado.
     - calcula delta = x - prev_x; convierte delta/dt -> omega (deg/s)
     - encola fila con (ts, ax,ay,az, gx,gy,gz, acc, gyro, sim_disp_um)
       -> el writer usará sim_disp_um (último campo) como desplazamiento verdadero
    """
    global last_ts_for_gyro
    print("Producer: SIMULATE progresivo (fase1 {}s, rango {}-{} µm) (fase2 > {}s, rango {}-{} µm)".format(
        SIM_PHASE1_SECONDS, SIM_MIN_UM, SIM_PHASE1_MAX_UM, SIM_PHASE1_SECONDS, SIM_PHASE1_MAX_UM, SIM_PHASE2_MAX_UM))

    last_ts_for_gyro = None
    prev_disp_um = max(SIM_MIN_UM, min(SIM_MEAN_UM, SIM_PHASE1_MAX_UM))
    x = prev_disp_um
    t_start = time.time()

    while not stop_event.is_set():
        ts = time.time()
        dt = produce_interval

        # determinar fase y límites
        elapsed = ts - t_start
        if elapsed < SIM_PHASE1_SECONDS:
            current_min = SIM_MIN_UM
            current_max = SIM_PHASE1_MAX_UM
            steps_needed = max(1.0, SIM_PHASE1_SECONDS / dt)
            dyn_step_cap = (current_max - current_min) * dt / (SIM_PHASE1_SECONDS)  # rango * dt / T => µm per step
        else:
            current_min = SIM_MIN_UM
            current_max = SIM_PHASE2_MAX_UM
            steps_needed = max(1.0, (SIM_PHASE1_SECONDS*2) / dt)
            dyn_step_cap = max((current_max - current_min) * dt / (SIM_PHASE1_SECONDS*2), SIM_MAX_STEP_UM)

        # OU step (ruido suave alrededor de la media)
        mu = SIM_MEAN_UM + (elapsed / 5.0) * 20.0
        theta = SIM_OU_THETA
        sigma = SIM_OU_SIGMA
        noise = random.gauss(0.0, 1.0)
        x = x + theta * (mu - x) * dt + sigma * math.sqrt(max(dt, 1e-9)) * noise

        # forzar límites
        if x < current_min: x = current_min
        if x > current_max: x = current_max

        # limitar cambio por paso combinando dyn_step_cap y SIM_MAX_STEP_UM
        step_cap = min(SIM_MAX_STEP_UM, max(dyn_step_cap, 0.0001))
        delta_um = x - prev_disp_um
        if delta_um > step_cap:
            delta_um = step_cap
            x = prev_disp_um + delta_um
        elif delta_um < -step_cap:
            delta_um = -step_cap
            x = prev_disp_um + delta_um

        # convertir delta_um --> omega (deg/s) mediante delta_s / (R * dt)
        delta_s_m = delta_um * 1e-6
        R_m = SENSOR_RADIUS_MM * 1e-3
        if R_m <= 0 or dt <= 0:
            omega_deg = 0.0
        else:
            dtheta_rad = 0.0
            if abs(delta_s_m) > 0.0:
                dtheta_rad = delta_s_m / R_m
            omega_rad = dtheta_rad / dt
            omega_deg = omega_rad * 180.0 / math.pi
            if not math.isfinite(omega_deg):
                omega_deg = 0.0
            # cap
            if omega_deg > SIM_MAX_OMEGA_DEG: omega_deg = SIM_MAX_OMEGA_DEG
            if omega_deg < -SIM_MAX_OMEGA_DEG: omega_deg = -SIM_MAX_OMEGA_DEG

        # distribuir en gx,gy (planar)
        theta_ang = random.random() * 2.0 * math.pi
        gx = omega_deg * math.cos(theta_ang)
        gy = omega_deg * math.sin(theta_ang)
        gz = 0.0

        # acelerómetro con ruido pequeño alrededor de g
        ax = random.normalvariate(0.0, 0.02)
        ay = random.normalvariate(0.0, 0.02)
        az = 9.81 + random.normalvariate(0.0, 0.02)
        acc = math.sqrt(ax*ax + ay*ay + az*az)
        gyro = math.sqrt(gx*gx + gy*gy + gz*gz)

        # encolamos la fila y añadimos sim_disp_um (x) como última columna
        row = [ts, ax, ay, az, gx, gy, gz, acc, gyro, x]
        try:
            row_queue.put(row, timeout=0.5)
        except queue.Full:
            pass

        prev_disp_um = x
        last_ts_for_gyro = ts
        time.sleep(produce_interval)
    
# ---------------- PRODUCER: SERIAL ------------------
def producer_serial(port=SERIAL_PORT, baud=BAUD_RATE):
    if serial is None:
        print("pyserial no instalado; no se puede usar 'serial' mode.")
        return
    print(f"Producer: SERIAL {port}@{baud}")
    try:
        ser = serial.Serial(port, baud, timeout=1.0)
        time.sleep(0.05)
    except Exception as e:
        print("Error abriendo serial:", e)
        return
    while not stop_event.is_set():
        try:
            line = ser.readline().decode('utf-8',errors='ignore').strip()
            if not line: continue
            parts = line.split(',')
            if len(parts) < 7: continue
            # form: ts_us,ax,ay,az,gx,gy,gz
            try:
                ts_us = float(parts[0]); ts_s = ts_us/1e6
                ax,ay,az = map(float,parts[1:4])
                gx,gy,gz = map(float,parts[4:7])
            except Exception:
                # fallback: ts_s direct
                try:
                    ts_s = float(parts[0]); ax,ay,az = map(float,parts[1:4]); gx,gy,gz = map(float,parts[4:7])
                except Exception:
                    continue
            acc = math.sqrt(ax*ax + ay*ay + az*az)
            gyro = math.sqrt(gx*gx + gy*gy + gz*gz)
            row = [ts_s, ax, ay, az, gx, gy, gz, acc, gyro]  # no sim_disp
            try: row_queue.put(row, timeout=0.5)
            except queue.Full: pass
        except Exception as e:
            print("Error serial:", e); time.sleep(0.05)
    try: ser.close()
    except: pass

# ---------------- CÁLCULOS -------------------------
def compute_disp_from_gyro(row, last_ts):
    """
    row: [ts,ax,ay,az,gx,gy,gz,acc,gyro]  (no sim_disp)
    Devuelve desplazamiento (mm) *instantáneo* estimado por giroscopio: s_mm = R * (omega_rad * dt)
    """
    ts = row[0]
    gx,gy,gz = row[4], row[5], row[6]
    omega_deg = math.sqrt(gx*gx + gy*gy + gz*gz)
    omega_rad = omega_deg * math.pi / 180.0
    if last_ts is None:
        return 0.0, ts
    dt = ts - last_ts
    if dt <= 0 or dt > 1.0:
        return 0.0, ts
    dtheta = omega_rad * dt
    s_m = abs((SENSOR_RADIUS_MM / 1000.0) * dtheta)
    s_mm = s_m * 1000.0
    return s_mm, ts

def compute_disp_from_acc_window(row):
    ts, ax, ay, az = row[0], row[1], row[2], row[3]
    acc_window.append((ts, ax, ay, az))
    while acc_window and (ts - acc_window[0][0] > ACC_WINDOW_SECONDS):
        acc_window.popleft()
    if len(acc_window) < 3:
        return 0.0
    xs = [item[0] for item in acc_window]
    axs = [item[1] for item in acc_window]
    ays = [item[2] for item in acc_window]
    azs = [item[3] for item in acc_window]
    mags = [math.sqrt(axs[i]*axs[i] + ays[i]*ays[i] + azs[i]*azs[i]) for i in range(len(axs))]
    mean_a = sum(mags)/len(mags)
    mags = [m - mean_a for m in mags]
    vs = [0.0]*len(mags)
    for i in range(1,len(mags)):
        dt = xs[i] - xs[i-1]
        vs[i] = vs[i-1] + 0.5*(mags[i]+mags[i-1])*dt
    mean_v = sum(vs)/len(vs)
    vs = [v - mean_v for v in vs]
    ss = [0.0]*len(vs)
    for i in range(1,len(vs)):
        dt = xs[i] - xs[i-1]
        ss[i] = ss[i-1] + 0.5*(vs[i]+vs[i-1])*dt
    s_vals_m = ss
    p2p_m = max(s_vals_m) - min(s_vals_m)
    p2p_mm = abs(p2p_m * 1000.0)
    return p2p_mm

# ---------------- WRITER (CSV + SQLite + eventos) --------------
def ensure_csv_header(path):
    exists = os.path.exists(path)
    f = open(path, 'a', newline='')
    w = csv.writer(f)
    if not exists:
        w.writerow(HEADER)
        f.flush()
    return f, w

def writer_thread_func(db_conn):
    global csv_file, csv_writer, last_ts_for_gyro
    csv_file, csv_writer = ensure_csv_header(MASTER_CSV)
    c = db_conn.cursor()
    last_flush = time.time()
    db_batch_count = 0
    DB_COMMIT_BATCH = 50

    # ensure events.csv header
    if not os.path.exists(EVENTS_CSV):
        with open(EVENTS_CSV, 'w', newline='') as fe:
            w = csv.writer(fe); w.writerow(['ts_s','disp_um','level','message'])

    while not stop_event.is_set():
        try:
            row = row_queue.get(timeout=1.0)
        except queue.Empty:
            if time.time() - last_flush > WRITE_FLUSH_INTERVAL:
                try: csv_file.flush()
                except: pass
                last_flush = time.time()
            continue

        # detectamos si la fila trae sim_disp (simulate) -> fila de length 10
        has_sim_disp = (len(row) >= 10)
        if has_sim_disp:
            sim_disp_um = float(row[9])  # valor absoluto objetivo en µm
            # si la fila trae sim_disp, preferimos usarlo como disp_um
            disp_um = sim_disp_um
            # también calculamos s_um_gyro y s_um_acc para registro analítico
            # para compute_disp_from_gyro necesitamos una row de 9 elementos (sin sim_disp)
            row9 = row[:9]
            s_mm_gyro, last_ts_for_gyro = compute_disp_from_gyro(row9, last_ts_for_gyro)
            s_um_gyro = s_mm_gyro * 1000.0
            s_mm_acc = compute_disp_from_acc_window(row9)
            s_um_acc = s_mm_acc * 1000.0
        else:
            # serial mode: no sim_disp -> calculos a partir del sensor
            s_mm_gyro, last_ts_for_gyro = compute_disp_from_gyro(row, last_ts_for_gyro)
            s_um_gyro = s_mm_gyro * 1000.0
            s_mm_acc = compute_disp_from_acc_window(row)
            s_um_acc = s_mm_acc * 1000.0
            # como no tenemos 'absolute' simulated value, tomamos estimador combinado
            disp_um = max(s_um_gyro, s_um_acc)

        # escribir fila completa en CSV/DB (llenamos columnas disp)
        full_row = None
        if has_sim_disp:
            # usamos row9 para las 9 primeras columnas
            row9 = row[:9]
            full_row = row9 + [s_um_gyro, s_um_acc, disp_um]
        else:
            full_row = row + [s_um_gyro, s_um_acc, disp_um]

        try:
            csv_writer.writerow(full_row)
        except Exception as e:
            print("Error escribiendo CSV:", e)

        # insertar en DB
        try:
            c.execute("""INSERT INTO measurements (ts_s,ax,ay,az,gx,gy,gz,acc_mag,gyro_mag,disp_um_gyro,disp_um_acc,disp_um_combined)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", tuple(full_row))
            db_batch_count += 1
            if db_batch_count >= DB_COMMIT_BATCH:
                db_conn.commit(); db_batch_count = 0
        except Exception as e:
            print("Error insert DB measurements:", e)

        # actualizar buffers (para plot)
        with plot_lock:
            times.append(full_row[0])
            acc_mag.append(full_row[7])
            gyro_mag.append(full_row[8])
            disp_um_buf.append(disp_um)

        # alarmas y log de eventos
        alert_and_log(disp_um, full_row[0], c)

        # flush periódico
        if time.time() - last_flush > WRITE_FLUSH_INTERVAL:
            try: csv_file.flush()
            except: pass
            last_flush = time.time()

    # al cerrar: commit y cerrar
    try:
        if db_batch_count > 0: db_conn.commit()
    except: pass
    try:
        if csv_file: csv_file.flush(); csv_file.close()
    except: pass

# --------------- LOG/ALARMAS -------------------
def log_event_to_files(ts_s, disp_um, level, message, cursor=None):
    try:
        if cursor is not None:
            cursor.execute("INSERT INTO events (ts_s, disp_um, level, message) VALUES (?,?,?,?)",
                           (ts_s, disp_um, level, message))
    except Exception as e:
        print("Error insert events DB:", e)
    try:
        with open(EVENTS_CSV, 'a', newline='') as fe:
            w = csv.writer(fe); w.writerow([ts_s, disp_um, level, message])
    except Exception as e:
        print("Error escribiendo events.csv:", e)

def alert_and_log(disp_um, ts_now, db_cursor):
    global current_state, warning_since
    if disp_um > THRESH_ALERT_UM:
        if current_state != "ALERT":
            current_state = "ALERT"
            msg = f"ALERTA INMEDIATA: Desplazamiento {disp_um:.1f} µm > {THRESH_ALERT_UM} µm"
            print(f"[{datetime.fromtimestamp(ts_now)}] {msg}")
            log_event_to_files(ts_now, disp_um, "ALERT", msg, db_cursor)
        return
    if disp_um > THRESH_OK_UM:
        if warning_since is None:
            warning_since = ts_now
        elapsed = ts_now - warning_since
        if elapsed >= SUSTAIN_SECONDS_MIN:
            if current_state != "WARNING":
                current_state = "WARNING"
                msg = f"ADVERTENCIA: Desplazamiento {disp_um:.1f} µm sostenido por {elapsed:.2f}s"
                print(f"[{datetime.fromtimestamp(ts_now)}] {msg}")
                log_event_to_files(ts_now, disp_um, "WARNING", msg, db_cursor)  
        elif elapsed >= SUSTAIN_SECONDS_MAX:    
            msg = f"ADVERTENCIA: Desplazamiento peligroso sostenido por {elapsed:.2f}s. Se recomienda tomar accion"
            print(f"[{datetime.fromtimestamp(ts_now)}] {msg}")
            log_event_to_files(ts_now, disp_um, "WARNING", msg, db_cursor)  
        return
    if disp_um <= THRESH_OK_UM:
        if current_state != "OK":
            elapsed = ts_now - warning_since
            msg = f"VOLVIO A OK: Desplazamiento {disp_um:.1f} µm luego de {elapsed:.2f}s en estado peligroso"
            print(f"[{datetime.fromtimestamp(ts_now)}] {msg}")
            log_event_to_files(ts_now, disp_um, "OK", msg, db_cursor)
        current_state = "OK"
        warning_since = None

# ---------------- PLOT -------------------------
fig, (ax1, ax2, ax3) = plt.subplots(3,1, figsize=(10,10))
line_acc, = ax1.plot([], [], label='Aceleración (m/s²)'); ax1.grid(True); ax1.legend()
line_gyro, = ax2.plot([], [], label='Giroscopio (°/s)'); ax2.grid(True); ax2.legend()
line_disp, = ax3.plot([], [], label='Microdesplazamiento (µm)'); ax3.grid(True); ax3.legend()
ax3.axhline(THRESH_OK_UM, linestyle='--', linewidth=1, label='OK threshold')
ax3.axhline(THRESH_ALERT_UM, linestyle='--', linewidth=1, label='ALERT threshold')
status_text = ax3.text(0.02, 0.95, '', transform=ax3.transAxes, fontsize=10, verticalalignment='top',
                       bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))

def safe_limits(data, pad=0.05):
    if not data: return -1.0, 1.0
    mn = min(data); mx = max(data)
    if math.isclose(mn,mx): mn -= 0.5; mx += 0.5
    rng = mx-mn
    return mn - pad*rng, mx + pad*rng

def update_plot(frame):
    with plot_lock:
        if not times: return line_acc, line_gyro, line_disp, status_text
        xs = list(times); accs = list(acc_mag); gys = list(gyro_mag); ds = list(disp_um_buf)
    t0 = xs[0]; x_rel = [xx - t0 for xx in xs]; tmax = x_rel[-1]
    line_acc.set_data(x_rel, accs); line_gyro.set_data(x_rel, gys); line_disp.set_data(x_rel, ds)
    xmin = max(0.0, tmax - HISTORY_SECONDS)
    ax1.set_xlim(xmin, tmax + 0.05); ax2.set_xlim(xmin, tmax + 0.05); ax3.set_xlim(xmin, tmax + 0.05)
    aymin, aymax = safe_limits(accs); gymin, gymax = safe_limits(gys); dmin, dmax = safe_limits(ds)
    ax1.set_ylim(aymin, aymax); ax2.set_ylim(gymin, gymax)
    ax3.set_ylim(min(dmin, -10), max(dmax*1.1, THRESH_ALERT_UM*1.2))
    status_text.set_text(f"Estado: {current_state}\nÚltimo disp: {ds[-1]:.1f} µm\nR(mm)={SENSOR_RADIUS_MM}")
    if current_state == "OK": ax3.set_facecolor((0.95,1.0,0.95))
    elif current_state == "WARNING": ax3.set_facecolor((1.0,1.0,0.8))
    else: ax3.set_facecolor((1.0,0.9,0.9))
    return line_acc, line_gyro, line_disp, status_text

# ---------------- MAIN --------------------------
def start_producer():
    if MODE == 'simulate':
        t = threading.Thread(target=producer_simulate, daemon=True)
    elif MODE == 'serial':
        t = threading.Thread(target=producer_serial, daemon=True)
    else:
        raise ValueError("MODE inválido")
    t.start(); return t

def main():
    print("Iniciando monitor — MODE =", MODE)
    if MODE == 'serial' and serial is None:
        print("pyserial no instalado. Instala pyserial para usar serial.")
        return

    db_conn = init_db(SQLITE_DB)
    writer = threading.Thread(target=writer_thread_func, args=(db_conn,), daemon=True)
    writer.start()
    prod = start_producer()
    ani = animation.FuncAnimation(fig, update_plot, interval=200, cache_frame_data=False)
    plt.tight_layout()
    try:
        plt.show()
    except KeyboardInterrupt:
        print("Interrumpido por teclado.")
    finally:
        stop_event.set()
        time.sleep(0.5)
        try: db_conn.commit(); db_conn.close()
        except: pass
        print("Cerrado.")

if __name__ == '__main__':
    main()



