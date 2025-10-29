// // src/main.cpp
// #include <Arduino.h>
// #include <Wire.h>
// #include <Adafruit_Sensor.h>
// #include <Adafruit_ADXL345_U.h>

// // --------- CONFIG ---------
// const char* DEVICE_ID = "sensor_004"; // identifica cada micro (ajustalo)
// #define SDA_PIN 21
// #define SCL_PIN 22
// #define SERIAL_BAUD 115200

// // WIFI / UDP (opcional) - poner ENABLE_WIFI = false si no querés WiFi
// #define ENABLE_WIFI false
// const char* WIFI_SSID = "TU_SSID";
// const char* WIFI_PASS = "TU_PASS";
// const char* DEST_IP = "186.0.228.202"; //"192.168.1.100"; // IP del receptor (pc con sensorWorker.py si usas UDP)
// const uint16_t DEST_PORT = 9000;
// const uint16_t LOCAL_UDP_PORT = 9001; // puerto local para bind (no necesario normalmente)
// // --------------------------

// Adafruit_ADXL345_Unified accel = Adafruit_ADXL345_Unified(12345);

// #if ENABLE_WIFI
//   #include <WiFi.h>
//   #include <WiFiUdp.h>
//   WiFiUDP Udp;
// #endif

// volatile bool streaming = false;
// unsigned long lastIdPrint = 0;
// const unsigned long ID_PRINT_INTERVAL = 10000; // ms

// void send_line_serial(const String &line) {
//   Serial.println(line);
// }

// #if ENABLE_WIFI
// void send_line_udp(const String &line) {
//   if (WiFi.status() == WL_CONNECTED) {
//     Udp.beginPacket(DEST_IP, DEST_PORT);
//     Udp.print(line);
//     Udp.endPacket();
//   }
// }
// #endif

// void publish_line(const String &line) {
//   send_line_serial(line);
//   #if ENABLE_WIFI
//     send_line_udp(line);
//   #endif
// }

// void setup() {
//   Serial.begin(SERIAL_BAUD);
//   delay(150);
//   Serial.printf("\n=== ADXL345 logger init (Device: %s) ===\n", DEVICE_ID);

//   // I2C init ESP32 pins
//   Wire.begin(SDA_PIN, SCL_PIN);
//   Wire.setClock(400000);

//   if (!accel.begin()) {
//     Serial.println("ERROR: ADXL345 no detectado. Verifica wiring y VCC (usar 3.3V).");
//     // envia ID igualmente para que el worker sepa que hay algo en el puerto
//     Serial.printf("ID,%s\n", DEVICE_ID);
//     while (true) {
//       delay(3000);
//       Serial.println("ADXL345 no encontrado. Esperando reset...");
//     }
//   }

//   // Imprime ID al boot
//   Serial.printf("ID,%s\n", DEVICE_ID);

//   #if ENABLE_WIFI
//     Serial.printf("Conectando a WiFi %s...\n", WIFI_SSID);
//     WiFi.begin(WIFI_SSID, WIFI_PASS);
//     unsigned long tstart = millis();
//     while (WiFi.status() != WL_CONNECTED && millis() - tstart < 8000) {
//       delay(200);
//     }
//     if (WiFi.status() == WL_CONNECTED) {
//       Serial.printf("WiFi OK, IP: %s\n", WiFi.localIP().toString().c_str());
//       Udp.begin(LOCAL_UDP_PORT);
//     } else {
//       Serial.println("WiFi NO conectado. UDP desactivado.");
//     }
//   #endif
// }

// String read_line_from_serial() {
//   static String buf = "";
//   while (Serial.available()) {
//     char c = (char)Serial.read();
//     if (c == '\r') continue;
//     if (c == '\n') {
//       String line = buf;
//       buf = "";
//       line.trim();
//       return line;
//     } else {
//       buf += c;
//     }
//   }
//   return String();
// }

// void handle_command(const String &cmd) {
//   if (cmd.startsWith("CMD,")) {
//     String arg = cmd.substring(4);
//     arg.trim();
//     if (arg.equalsIgnoreCase("START")) {
//       streaming = true;
//       Serial.println("ACK,START");
//     } else if (arg.equalsIgnoreCase("STOP")) {
//       streaming = false;
//       Serial.println("ACK,STOP");
//     }
//   }
// }

// void loop() {
//   // leer comandos linea a linea
//   String ln = read_line_from_serial();
//   if (ln.length() > 0) handle_command(ln);

//   // imprimir ID periodicamente (por si el worker abre puerto y necesita mapear)
//   if (millis() - lastIdPrint > ID_PRINT_INTERVAL) {
//     Serial.printf("ID,%s\n", DEVICE_ID);
//     lastIdPrint = millis();
//   }

//   if (streaming) {
//     sensors_event_t event;
//     accel.getEvent(&event); // m/s^2

//     // timestamp en microsegundos (para compatibilidad con tu parser)
//     unsigned long ts_us = micros();
//     // Formato que espera sensorWorker: DEVICE_ID,ts_us,ax,ay,az
//     char line[128];
//     snprintf(line, sizeof(line), "%s,%.0lu,%.3f,%.3f,%.3f",
//              DEVICE_ID, (unsigned long)ts_us,
//              event.acceleration.x,
//              event.acceleration.y,
//              event.acceleration.z);
//     publish_line(String(line));
//   }

//   delay(50); // ~20Hz, ajusta según SAMPLE_INTERVAL en tu worker
// }
