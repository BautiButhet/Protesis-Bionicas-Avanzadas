#include <Arduino.h>
#include <SPI.h>
#include "ICM42688.h"

#define CS_PIN 5  // Ajustá este pin como chip select (CS) del ICM-42688

// SPI bus y frecuencia
ICM42688_FIFO imu(SPI, CS_PIN, 8000000); // SPI, CS pin, velocidad alta (8 MHz)

const float SENSOR_ODR_HZ = 1000.0;  // Tasa de muestreo esperada (en Hz)
uint32_t lastReadMs = 0;
const uint32_t readIntervalMs = 50; // Intervalo de lectura (cada 50 ms)

void setup() {
  Serial.begin(115200);
  while (!Serial);

  pinMode(CS_PIN, OUTPUT);
  digitalWrite(CS_PIN, HIGH);

  SPI.begin();  // Iniciar interfaz SPI

  // Inicializar sensor
  int status = imu.begin();
  if (status < 0) {
    Serial.println("ERROR iniciando ICM-42688");
    while (1) { delay(1000); }
  }
  Serial.println("ICM-42688 iniciado correctamente.");

  // Rangos de operación más sensibles
  imu.setAccelFS(ICM42688::gpm2);    // ±2g
  imu.setGyroFS(ICM42688::dps250);   // ±250 °/s

  // Configurar FIFO (acelerómetro + giroscopio)
  imu.enableFifo(true, true, false);
  imu.enableDataReadyInterrupt();
  Serial.println("FIFO activado; INT data-ready también.");
}

void loop() {
  uint32_t now = millis();
  if (now - lastReadMs >= readIntervalMs) {
    lastReadMs = now;

    if (imu.readFifo() < 0) {
      Serial.println("Overflow o error en FIFO. Reseteando...");
      delay(5);
      imu.enableFifo(true, true, false);
      return;
    }

    size_t n = 0;
    imu.getFifoAccelX_mss(&n, nullptr);
    if (n == 0) return;  // Si no hay muestras, salto

    float ax[n], ay[n], az[n], gx[n], gy[n], gz[n];

    imu.getFifoAccelX_mss(&n, ax);
    imu.getFifoAccelY_mss(&n, ay);
    imu.getFifoAccelZ_mss(&n, az);

    size_t n2 = 0;
    imu.getFifoGyroX(&n2, gx);
    imu.getFifoGyroY(&n2, gy);
    imu.getFifoGyroZ(&n2, gz);

    n = min(n, n2); // número de muestras coherente

    unsigned long t_read = micros();
    double dt_us = 1000000.0 / SENSOR_ODR_HZ;

    for (size_t i = 0; i < n; ++i) {
      unsigned long ts = (unsigned long)(t_read - (unsigned long)((n - 1 - i) * dt_us));
      Serial.printf("%lu,%.6f,%.6f,%.6f,%.6f,%.6f,%.6f\n",
                    ts, ax[i], ay[i], az[i], gx[i], gy[i], gz[i]);
    }
  }
}

//comentario chupete chupeton