import serial
import matplotlib.pyplot as plt
import matplotlib.animation as animation
import csv
import sys

# ----- Configuración -----
SERIAL_PORT = 'COM3'  # Cambiá según tu sistema: /dev/ttyUSB0, /dev/ttyACM0, etc.
BAUD_RATE = 115200

CSV_FILE = 'imu_data.csv'

# ----- Gráfico en vivo -----
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8))
plt.tight_layout(pad=3)

xs = []
acc_mag = []
gyro_mag = []

line_acc, = ax1.plot([], [], label='Aceleración (m/s²)')
ax1.set_title('Aceleración resultante')
ax1.set_xlabel('Tiempo (s)')
ax1.set_ylabel('Acele (m/s²)')
ax1.legend()
ax1.grid(True)

line_gyro, = ax2.plot([], [], label='Giroscopio (°/s)', color='orange')
ax2.set_title('Velocidad Angular resultante')
ax2.set_xlabel('Tiempo (s)')
ax2.set_ylabel('Giros (°/s)')
ax2.legend()
ax2.grid(True)

start_time = None

def update(frame):
    global start_time
    try:
        line = ser.readline().decode('utf-8').strip()
        if not line:
            return line_acc, line_gyro

        # CSV: timestamp, ax, ay, az, gx, gy, gz
        parts = line.split(',')
        if len(parts) != 7:
            return line_acc, line_gyro

        ts = float(parts[0]) / 1e6  # timestamp en segundos
        ax, ay, az = map(float, parts[1:4])
        gx, gy, gz = map(float, parts[4:7])

        acc = (ax**2 + ay**2 + az**2)**0.5
        gyro = (gx**2 + gy**2 + gz**2)**0.5

        if start_time is None:
            start_time = ts
        t = ts - start_time

        xs.append(t)
        acc_mag.append(acc)
        gyro_mag.append(gyro)

        line_acc.set_data(xs, acc_mag)
        line_gyro.set_data(xs, gyro_mag)

        ax1.set_xlim(max(0, t-10), t+0.1)
        ax1.set_ylim(min(acc_mag)-0.1, max(acc_mag)+0.1)
        ax2.set_xlim(max(0, t-10), t+0.1)
        ax2.set_ylim(min(gyro_mag)-1, max(gyro_mag)+1)

        # Grabá en archivo CSV
        csv_writer.writerow([ts, ax, ay, az, gx, gy, gz, acc, gyro])

    except Exception as e:
        print("Error al procesar:", e)

    return line_acc, line_gyro

if __name__ == '__main__':
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
    except Exception as e:
        print("Error con puerto serial:", e)
        sys.exit(1)

    f = open(CSV_FILE, 'w', newline='')
    csv_writer = csv.writer(f)
    header = ['ts_s', 'ax', 'ay', 'az', 'gx', 'gy', 'gz', 'acc_mag', 'gyro_mag']
    csv_writer.writerow(header)

    ani = animation.FuncAnimation(fig, update, interval=50)
    plt.show()

    f.close()
    ser.close()
