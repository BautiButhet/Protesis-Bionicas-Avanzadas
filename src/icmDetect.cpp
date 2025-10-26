// #include <Arduino.h>

// #include <Wire.h>
// // Diagnostico IMU avanzado para ESP32
// #include <Wire.h>

// #define SDA_PIN 21
// #define SCL_PIN 22

// // Si tenés RST o AD0 conectados a un GPIO del ESP32, pon su número acá.
// // Si no, dejá -1.
// const int RST_PIN = -1;   // ej: 15
// const int AD0_PIN = -1;   // ej: 4

// // Registros WHO_AM_I probados
// const uint8_t who_regs[] = {0x75, 0x00, 0x70, 0x71};

// // Lista direcciones 7-bit comunes y sus 8-bit (8-bit = 7-bit << 1)
// const uint8_t addrs7[] = {0x68, 0x69, 0x1E, 0x0E}; // ampliá si querés
// const int NADDR7 = sizeof(addrs7)/sizeof(addrs7[0]);

// void hwReset() {
//   if (RST_PIN < 0) return;
//   pinMode(RST_PIN, OUTPUT);
//   digitalWrite(RST_PIN, LOW);
//   delay(50);
//   digitalWrite(RST_PIN, HIGH);
//   delay(200);
// }

// void forceAD0(bool hi) {
//   if (AD0_PIN < 0) return;
//   pinMode(AD0_PIN, OUTPUT);
//   digitalWrite(AD0_PIN, hi ? HIGH : LOW);
//   delay(50);
// }

// void tryRead(uint8_t addr7, uint8_t reg, bool useRepeatedStart) {
//   Serial.printf("Probe ADDR 0x%02X REG 0x%02X (RS=%d): ", addr7, reg, useRepeatedStart);
//   Wire.beginTransmission(addr7);
//   Wire.write(reg);
//   uint8_t err = Wire.endTransmission(useRepeatedStart ? false : true);
//   if (err != 0) {
//     Serial.printf("endTransmission err=%u\n", err);
//     return;
//   }
//   int n = Wire.requestFrom(addr7, (uint8_t)1);
//   if (n == 1) {
//     uint8_t v = Wire.read();
//     Serial.printf("OK -> 0x%02X\n", v);
//   } else {
//     Serial.println("NO DATA (requestFrom returned 0)");
//   }
// }

// void scanAllSpeedsAndModes() {
//   // probamos 2 velocidades: 400k y 100k
//   int speeds[] = {400000, 100000};
//   for (int sp = 0; sp < 2; ++sp) {
//     Wire.setClock(speeds[sp]);
//     Serial.printf("\n>>> I2C speed: %u Hz\n", speeds[sp]);

//     // si hay RST, hacer reset
//     hwReset();

//     // probar AD0 = GND primero
//     forceAD0(false);

//     for (int i = 0; i < NADDR7; ++i) {
//       uint8_t a7 = addrs7[i];
//       // lectura repeated start
//       for (uint8_t r = 0; r < sizeof(who_regs); ++r) {
//         tryRead(a7, who_regs[r], true);
//       }
//       // tambien probamos la "lectura stop" por si cambia comportamiento
//       for (uint8_t r = 0; r < sizeof(who_regs); ++r) {
//         tryRead(a7, who_regs[r], false);
//       }
//       // probar la 8-bit (solo info)
//       Serial.printf("  (8-bit equiv = 0x%02X)\n", a7 << 1);
//     }

//     // si hay AD0 configurable, probarlo en HIGH
//     if (AD0_PIN >= 0) {
//       Serial.println("Forzando AD0 HIGH y reintentando...");
//       forceAD0(true);
//       delay(100);
//       for (int i = 0; i < NADDR7; ++i) {
//         uint8_t a7 = addrs7[i];
//         for (uint8_t r = 0; r < sizeof(who_regs); ++r) tryRead(a7, who_regs[r], true);
//       }
//       forceAD0(false);
//     }
//   }
// }

// void setup() {
//   Serial.begin(115200);
//   delay(200);
//   Serial.println("\n=== Diagnostico IMU avanzado ===");
//   if (RST_PIN >= 0) Serial.printf("RST_PIN configured: %d\n", RST_PIN);
//   if (AD0_PIN >= 0) Serial.printf("AD0_PIN configured: %d\n", AD0_PIN);
//   Wire.begin(SDA_PIN, SCL_PIN);
//   delay(50);

//   // primero scan basico para ver devices ack
//   Serial.println("\nI2C quick scan:");
//   for (uint8_t addr = 1; addr < 127; ++addr) {
//     Wire.beginTransmission(addr);
//     uint8_t e = Wire.endTransmission();
//     if (e == 0) {
//       Serial.printf(" Found device at 0x%02X\n", addr);
//     }
//   }

//   // ahora probe detallado
//   scanAllSpeedsAndModes();

//   Serial.println("\n=== Diagnostico completo: revisar salidas ===");
// }

// void loop() {
//   delay(1000);
// }



