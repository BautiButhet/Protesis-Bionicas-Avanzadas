// esp32_sensor.ino
// Sketch mínimo para ESP32 + ICM42688 (igual que tu sensor) que:
// - Manda "ID,<DEVICE_ID>" al arrancar
// - Espera comandos por Serial: "CMD,START" o "CMD,STOP"
// - Cuando está START manda líneas: "<DEVICE_ID>,<timestamp_us>,<ax>,<ay>,<az>"
// - No usa Wi-Fi

#include <Arduino.h>
#include <DFRobot_ICM42688.h>

// Ajustalo a tu device id (único por cada ESP)
#define DEVICE_ID "sensor_004"

// Dirección I2C del sensor
#define DFRobot_ICM42688_I2C_L_ADDR 0x68
DFRobot_ICM42688_I2C ICM42688(DFRobot_ICM42688_I2C_L_ADDR);

bool sending_enabled = false;
unsigned long last_send_ms = 0;
const unsigned long SEND_INTERVAL_MS = 1000; // 1s 

void setup() {
  Serial.begin(115200);
  // Esperar Serial listo (útil en pruebas)
  unsigned long start = millis();
  while (!Serial && millis() - start < 2000) { delay(10); }

  // Inicializar sensor (reintenta)
  while (ICM42688.begin() != 0) {
    delay(1000);
  }
  ICM42688.setODRAndFSR(ACCEL, ODR_1KHZ, FSR_0);
  ICM42688.startAccelMeasure(LN_MODE);
  ICM42688.startFIFOMode();

  // Enviar ID una vez al iniciar para que el gateway pueda reconocer el device si hace dynamic mapping
  Serial.print("ID,");
  Serial.println(DEVICE_ID);
}

void loop() {
  // Leer comandos por serial (no bloqueante)
  while (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    line.trim();
    if (line.length() == 0) continue;
    // posible formato: "CMD,START" o "CMD,STOP"
    if (line.startsWith("CMD,")) {
      String cmd = line.substring(4);
      cmd.toUpperCase();
      if (cmd == "START") {
        sending_enabled = true;
        // opcional: confirmar al gateway
        Serial.print("ACK,");
        Serial.print(DEVICE_ID);
        Serial.println(",STARTED");
      } else if (cmd == "STOP") {
        sending_enabled = false;
        Serial.print("ACK,");
        Serial.print(DEVICE_ID);
        Serial.println(",STOPPED");
      }
    }
  }

  // Si está habilitado, leer sensor y enviar cada SEND_INTERVAL_MS
  if (sending_enabled) {
      // Leer FIFO/valores
      ICM42688.getFIFOData();
      float ax = ICM42688.getAccelDataX();
      float ay = ICM42688.getAccelDataY();
      float az = ICM42688.getAccelDataZ();
      unsigned long ts_us = micros();
      // Formato: DEVICE_ID,timestamp_us,ax,ay,az
      Serial.print(DEVICE_ID);
      Serial.print(',');
      Serial.print(ts_us);
      Serial.print(',');
      Serial.print(ax, 6);
      Serial.print(',');
      Serial.print(ay, 6);
      Serial.print(',');
      Serial.println(az, 6);
    }
  else {
    // si está detenido, no hacer lecturas intensivas. Pequeña espera.
    delay(50);
  }
}
