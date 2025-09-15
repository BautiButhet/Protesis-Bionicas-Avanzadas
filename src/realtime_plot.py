"""
Monitor de micro-desplazamiento.
- MODOS: 'simulate' | 'serial'
- CSV: main.csv
- SQLite: main.db
- events.csv: registro de eventos
"""

import os, csv, time, math, threading, queue, random, sqlite3
from collections import deque
from datetime import datetime
try:
    import serial
except Exception:
    serial = None
import matplotlib.pyplot as plt
import matplotlib.animation as animation

# -------- CONFIG -----------
MODE = 'simulate'         # 'simulate' | 'serial'
SERIAL_PORT = 'COM3'
BAUD_RATE = 115200

SESSION_FOLDER = '.'
MASTER_CSV = os.path.join(SESSION_FOLDER, 'main.csv')
SQLITE_DB = os.path.join(SESSION_FOLDER, 'main.db')
EVENTS_CSV = os.path.join(SESSION_FOLDER, 'events.csv')

HISTORY_SECONDS = 30.0
MAX_POINTS = 2000
WRITE_FLUSH_INTERVAL = 1.0

# físico
SENSOR_RADIUS_MM = 30.0
ACC_WINDOW_SECONDS = 1.0

# umbrales (µm)
THRESH_OK_UM = 50
THRESH_WARNING_UM = 100.0
THRESH_ALERT_UM = 150.0
SUSTAIN_SECONDS_MIN = 2.5
SUSTAIN_SECONDS_MAX = 10

# simulación
SIM_MIN_UM = 5.0
SIM_PHASE1_MAX_UM = 100.0
SIM_PHASE2_MAX_UM = 200.0
SIM_MEAN_UM = 40.0
SIM_PRODUCE_INTERVAL = 0.05
SIM_PHASE1_SECONDS = 15.0
SIM_OU_THETA = 0.6
SIM_OU_SIGMA = 0.8
SIM_MAX_STEP_UM = 5.0
SIM_MAX_OMEGA_DEG = 400.0

# Cabecera CSV (coherente con DB)
HEADER = ['ts_s','ax','ay','az','acc_mag','disp_um_acc','disp_um_combined']

# Estructuras
row_queue = queue.Queue(maxsize=20000)
plot_lock = threading.Lock()
times = deque(maxlen=MAX_POINTS)
acc_mag = deque(maxlen=MAX_POINTS)
disp_um_buf = deque(maxlen=MAX_POINTS)

stop_event = threading.Event()
csv_file = None; csv_writer = None

acc_window = deque()
last_ts_for_gyro = None  # no usado actualmente

# estado alarmas
current_state = "OK"
warning_since = None
start_time = time.time()

# ---------- DB ----------
def init_db(path):
    conn = sqlite3.connect(path, check_same_thread=False)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS measurements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_s REAL,
            ax REAL, ay REAL, az REAL,
            acc_mag REAL,
            disp_um_acc REAL,
            disp_um_combined REAL
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

# ---------- PRODUCER SIMULATE ----------
def producer_simulate(produce_interval=SIM_PRODUCE_INTERVAL):
    """
    Encola filas tipo:
      - simulate: [ts_s, ax, ay, az, acc_mag, sim_disp_um]  (len==6)
    """
    print(f"Producer: SIMULATE (phase1 {SIM_PHASE1_SECONDS}s, phase1_max {SIM_PHASE1_MAX_UM}µm)")
    prev_disp_um = max(SIM_MIN_UM, min(SIM_MEAN_UM, SIM_PHASE1_MAX_UM))
    x = prev_disp_um
    t_start = time.time()

    while not stop_event.is_set():
        ts = time.time()
        dt = produce_interval

        elapsed = ts - t_start
        if elapsed < SIM_PHASE1_SECONDS:
            current_min = SIM_MIN_UM
            current_max = SIM_PHASE1_MAX_UM
            dyn_step_cap = (current_max - current_min) * dt / (SIM_PHASE1_SECONDS)
        else:
            current_min = SIM_MIN_UM
            current_max = SIM_PHASE2_MAX_UM
            dyn_step_cap = max((current_max - current_min) * dt / (SIM_PHASE1_SECONDS*2), SIM_MAX_STEP_UM)

        mu = SIM_MEAN_UM + (elapsed / 5.0) * 20.0
        theta = SIM_OU_THETA
        sigma = SIM_OU_SIGMA
        noise = random.gauss(0.0, 1.0)
        x = x + theta * (mu - x) * dt + sigma * math.sqrt(max(dt, 1e-9)) * noise

        x = max(current_min, min(current_max, x))

        step_cap = min(SIM_MAX_STEP_UM, max(dyn_step_cap, 1e-6))
        delta_um = x - prev_disp_um
        if delta_um > step_cap:
            delta_um = step_cap; x = prev_disp_um + delta_um
        elif delta_um < -step_cap:
            delta_um = -step_cap; x = prev_disp_um + delta_um

        # acelerómetro simplificado: g + ruido pequeño
        ax = random.normalvariate(0.0, 0.02)
        ay = random.normalvariate(0.0, 0.02)
        az = 9.81 + random.normalvariate(0.0, 0.02)
        acc = math.sqrt(ax*ax + ay*ay + az*az)

        row = [ts, ax, ay, az, acc, x]
        try:
            row_queue.put(row, timeout=0.5)
        except queue.Full:
            pass

        prev_disp_um = x
        time.sleep(produce_interval)

# ---------- PRODUCER SERIAL ----------
def producer_serial(port=SERIAL_PORT, baud=BAUD_RATE):
    if serial is None:
        print("pyserial no instalado; modo 'serial' no disponible.")
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
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            if not line: continue
            parts = line.split(',')
            # Arduino imprime: ts_us,ax,ay,az
            if len(parts) < 4:
                continue
            try:
                ts_us = float(parts[0])
                ts_s = ts_us / 1e6
                ax = float(parts[1]); ay = float(parts[2]); az = float(parts[3])
            except Exception:
                continue
            acc = math.sqrt(ax*ax + ay*ay + az*az)
            row = [ts_s, ax, ay, az, acc]   # len==5
            try: row_queue.put(row, timeout=0.5)
            except queue.Full: pass
        except Exception as e:
            print("Error serial:", e); time.sleep(0.05)
    try: ser.close()
    except: pass

# ---------- CÁLCULOS ----------
def compute_disp_from_acc_window(row):
    """
    row: [ts_s, ax, ay, az]
    Integra ventana (ACC_WINDOW_SECONDS) p/ estimar pico-a-pico de desplazamiento (m) -> devuelve p2p (mm)
    Método: quitar media de aceleración, integrar trapezoidal para velocidad, quitar mean, integrar para posición, devolver p2p.
    """
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
    mags = [m - mean_a for m in mags]  # high-pass remove gravity bias (aprox)
    # integrar -> velocidad
    vs = [0.0]*len(mags)
    for i in range(1, len(mags)):
        dt = xs[i] - xs[i-1]
        if dt <= 0: dt = 1e-6
        vs[i] = vs[i-1] + 0.5*(mags[i] + mags[i-1]) * dt
    # remove mean velocity (evita drift constante)
    mean_v = sum(vs)/len(vs)
    vs = [v - mean_v for v in vs]
    # integrar -> posicion
    ss = [0.0]*len(vs)
    for i in range(1, len(vs)):
        dt = xs[i] - xs[i-1]
        if dt <= 0: dt = 1e-6
        ss[i] = ss[i-1] + 0.5*(vs[i] + vs[i-1]) * dt
    # p2p en metros -> convertir mm
    p2p_m = max(ss) - min(ss)
    p2p_mm = abs(p2p_m * 1000.0)
    return p2p_mm

# ---------- CSV / WRITER ----------
def ensure_csv_header(path):
    exists = os.path.exists(path)
    f = open(path, 'a', newline='')
    w = csv.writer(f)
    if not exists:
        w.writerow(HEADER); f.flush()
    return f, w

def writer_thread_func(db_conn):
    global csv_file, csv_writer
    csv_file, csv_writer = ensure_csv_header(MASTER_CSV)
    c = db_conn.cursor()
    last_flush = time.time()
    db_batch_count = 0
    DB_COMMIT_BATCH = 50

    # events header
    if not os.path.exists(EVENTS_CSV):
        with open(EVENTS_CSV, 'w', newline='') as fe:
            csv.writer(fe).writerow(['ts_s','disp_um','level','message'])

    # estadística de validación (modo simulate)
    sim_count = 0
    sim_error_sq = 0.0

    while not stop_event.is_set():
        try:
            row = row_queue.get(timeout=1.0)
        except queue.Empty:
            if time.time() - last_flush > WRITE_FLUSH_INTERVAL:
                try: csv_file.flush()
                except: pass
                last_flush = time.time()
            continue

        # distinguir tipos de fila por longitud:
        # simulate -> len==6: [ts,ax,ay,az,acc, sim_disp_um]
        # serial   -> len==5: [ts,ax,ay,az,acc]
        try:
            if len(row) == 6:
                ts = float(row[0]); ax_v = float(row[1]); ay_v = float(row[2]); az_v = float(row[3])
                acc_mag_v = float(row[4]); sim_disp_um = float(row[5])
                s_mm_acc = compute_disp_from_acc_window([ts, ax_v, ay_v, az_v])
                s_um_acc = s_mm_acc * 1000.0
                disp_um_combined = sim_disp_um  # ground truth in simulate mode
                # Validación: comparar estimado por acelerómetro con sim_disp
                err = s_um_acc - sim_disp_um
                sim_count += 1
                sim_error_sq += err*err
                if sim_count % 50 == 0:
                    rms = math.sqrt(sim_error_sq / sim_count) if sim_count > 0 else 0.0
                    print(f"[SIM VAL] muestras={sim_count} err_inst={err:.1f}µm RMS={rms:.2f}µm")
                full_row = [ts, ax_v, ay_v, az_v, acc_mag_v, s_um_acc, disp_um_combined]

            elif len(row) == 5:
                ts = float(row[0]); ax_v = float(row[1]); ay_v = float(row[2]); az_v = float(row[3])
                acc_mag_v = float(row[4])
                s_mm_acc = compute_disp_from_acc_window([ts, ax_v, ay_v, az_v])
                s_um_acc = s_mm_acc * 1000.0
                disp_um_combined = s_um_acc
                full_row = [ts, ax_v, ay_v, az_v, acc_mag_v, s_um_acc, disp_um_combined]
            else:
                # fila inesperada: descartar
                print("Fila con formato inesperado, longitud=", len(row))
                continue
        except Exception as e:
            print("Error procesando fila:", e)
            continue

        # escribir CSV
        try:
            csv_writer.writerow(full_row)
        except Exception as e:
            print("Error escribiendo CSV:", e)

        # insertar DB (coincidir con schema)
        try:
            c.execute("""INSERT INTO measurements (ts_s,ax,ay,az,acc_mag,disp_um_acc,disp_um_combined)
                         VALUES (?,?,?,?,?,?,?)""", tuple(full_row))
            db_batch_count += 1
            if db_batch_count >= DB_COMMIT_BATCH:
                db_conn.commit(); db_batch_count = 0
        except Exception as e:
            print("Error insert DB measurements:", e)

        # actualizar buffers para plot
        with plot_lock:
            times.append(full_row[0])
            acc_mag.append(full_row[4])
            disp_um_buf.append(full_row[6])

        # alarmas y log de eventos (usa disp_um_combined)
        alert_and_log(full_row[6], full_row[0], c)

        # flush periódico
        if time.time() - last_flush > WRITE_FLUSH_INTERVAL:
            try: csv_file.flush()
            except: pass
            last_flush = time.time()

    # al cerrar
    try:
        if db_batch_count > 0: db_conn.commit()
    except: pass
    try:
        if csv_file: csv_file.flush(); csv_file.close()
    except: pass

# ---------- LOG/ALARMAS ----------
def log_event_to_files(ts_s, disp_um, level, message, cursor=None):
    try:
        if cursor is not None:
            cursor.execute("INSERT INTO events (ts_s, disp_um, level, message) VALUES (?,?,?,?)",
                           (ts_s, disp_um, level, message))
    except Exception as e:
        print("Error insert events DB:", e)
    try:
        with open(EVENTS_CSV, 'a', newline='') as fe:
            csv.writer(fe).writerow([ts_s, disp_um, level, message])
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
        return
    if disp_um <= THRESH_OK_UM:
        if current_state != "OK":
            elapsed = ts_now - (warning_since or ts_now)
            msg = f"VOLVIO A OK: Desplazamiento {disp_um:.1f} µm luego de {elapsed:.2f}s"
            print(f"[{datetime.fromtimestamp(ts_now)}] {msg}")
            log_event_to_files(ts_now, disp_um, "OK", msg, db_cursor)
        current_state = "OK"
        warning_since = None

# ---------- PLOT ----------
fig, (ax1, ax_disp) = plt.subplots(2, 1, figsize=(10, 8))
line_acc, = ax1.plot([], [], label='Aceleración (m/s²)'); ax1.grid(True); ax1.legend()
line_disp, = ax_disp.plot([], [], label='Microdesplazamiento (µm)'); ax_disp.grid(True); ax_disp.legend()
ax_disp.axhline(THRESH_OK_UM, linestyle='--', linewidth=1, label='OK threshold')
ax_disp.axhline(THRESH_ALERT_UM, linestyle='--', linewidth=1, label='ALERT threshold')
status_text = ax_disp.text(0.02, 0.95, '', transform=ax_disp.transAxes, fontsize=10, verticalalignment='top',
                           bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))

def safe_limits(data, pad=0.05):
    if not data: return -1.0, 1.0
    mn = min(data); mx = max(data)
    if math.isclose(mn, mx): mn -= 0.5; mx += 0.5
    rng = mx - mn
    return mn - pad*rng, mx + pad*rng

def update_plot(frame):
    with plot_lock:
        if not times:
            return line_acc, line_disp, status_text
        xs = list(times); accs = list(acc_mag); ds = list(disp_um_buf)
    t0 = xs[0]; x_rel = [xx - t0 for xx in xs]; tmax = x_rel[-1]
    line_acc.set_data(x_rel, accs); line_disp.set_data(x_rel, ds)
    xmin = max(0.0, tmax - HISTORY_SECONDS)
    ax1.set_xlim(xmin, tmax + 0.05); ax_disp.set_xlim(xmin, tmax + 0.05)
    aymin, aymax = safe_limits(accs); dmin, dmax = safe_limits(ds)
    ax1.set_ylim(aymin, aymax)
    ax_disp.set_ylim(min(dmin, -10), max(dmax*1.1, THRESH_ALERT_UM*1.2))
    status_text.set_text(f"Estado: {current_state}\nÚltimo disp: {ds[-1]:.1f} µm\nR(mm)={SENSOR_RADIUS_MM}")
    if current_state == "OK": ax_disp.set_facecolor((0.95,1.0,0.95))
    elif current_state == "WARNING": ax_disp.set_facecolor((1.0,1.0,0.8))
    else: ax_disp.set_facecolor((1.0,0.9,0.9))
    return line_acc, line_disp, status_text

# ---------- MAIN ----------
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
