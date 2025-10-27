// // I2C probe directo para 0x68 y 0x7E
// // Muestra error exacto y explicación humana
// #include <Arduino.h>
// #include <Wire.h>

// #define SDA_PIN 21
// #define SCL_PIN 22

// const uint8_t ADDR_A = 0x68;
// const uint8_t ADDR_B = 0x7E;
// const uint8_t REGS[] = { 0x75, 0x00, 0x47 }; // WHO_AM_I y registros candidatos

// // Interpreta el valor devuelto por Wire.endTransmission()
// const char* endTransmissionExplain(uint8_t code) {
//   switch (code) {
//     case 0: return "OK (ACK recibido)";
//     case 1: return "Data too long to fit in transmit buffer (datos demasiado largos)";
//     case 2: return "Received NACK on transmit of address (NACK en la dirección) -> dispositivo no responde a esta dirección";
//     case 3: return "Received NACK on transmit of data (NACK en datos) -> dispositivo NACKea el registro";
//     case 4: return "Other error (bus busy/timeouts) -> bus/I2C driver falló";
//     default: return "Codigo desconocido";
//   }
// }

// void printHexBuffer(uint8_t *buf, size_t len) {
//   for (size_t i = 0; i < len; ++i) {
//     if (buf[i] < 0x10) Serial.print('0');
//     Serial.print(buf[i], HEX);
//     if (i + 1 < len) Serial.print(' ');
//   }
//   Serial.println();
// }

// void probeAddress(uint8_t addr) {
//   Serial.printf("\n=== Probe address 0x%02X ===\n", addr);

//   for (uint8_t rep = 0; rep < 2; ++rep) {
//     bool repeatedStart = (rep == 0); // primero repeated-start=true, luego false (stop)
//     Serial.printf("\n-- Intento con repeatedStart=%d --\n", repeatedStart ? 1 : 0);

//     for (uint8_t ri = 0; ri < sizeof(REGS); ++ri) {
//       uint8_t reg = REGS[ri];
//       Serial.printf("Leyendo REG 0x%02X : ", reg);

//       // Primera fase: enviar el registro
//       Wire.beginTransmission(addr);
//       Wire.write(reg);
//       uint8_t et = Wire.endTransmission(repeatedStart ? false : true);

//       // Imprime error/estado del endTransmission
//       Serial.printf("endTransmission=%u (%s)\n", et, endTransmissionExplain(et));
//       if (et != 0) {
//         // Si hubo error en endTransmission ya tenemos pista
//         continue;
//       }

//       // Segunda fase: solicitar 1 byte
//       int n = Wire.requestFrom((int)addr, (uint8_t)1);
//       Serial.printf("  requestFrom() returned %d bytes -> ", n);
//       if (n == 1) {
//         uint8_t v = Wire.read();
//         Serial.printf("DATA = 0x%02X\n", v);
//       } else if (n == 0) {
//         Serial.println("NO DATA (requestFrom returned 0) -> el esclavo ACKed pero no devolvió bytes");
//       } else {
//         Serial.printf(" %d bytes (leer): ", n);
//         uint8_t tmp[16];
//         int i=0;
//         while(Wire.available() && i < 16) tmp[i++] = Wire.read();
//         printHexBuffer(tmp, i);
//       }

//       delay(20);
//     }
//   }

//   // Lectura "bruta" de bloque (si el slave permite burst)
//   Serial.println("\nIntento lectura bloque (14 bytes) - útil para sensores que devuelven burst:");
//   Wire.beginTransmission(addr);
//   Wire.write(REGS[0]); // intentar desde 0x75 o 0x00 (ya definido arriba)
//   uint8_t et2 = Wire.endTransmission(false);
//   Serial.printf(" endTransmission=%u (%s)\n", et2, endTransmissionExplain(et2));
//   if (et2 == 0) {
//     int n = Wire.requestFrom((int)addr, (uint8_t)14);
//     Serial.printf(" requestFrom(14) returned %d\n", n);
//     if (n > 0) {
//       uint8_t buf[14];
//       int i=0;
//       while (Wire.available() && i < n) buf[i++] = Wire.read();
//       Serial.print(" Raw bytes: ");
//       printHexBuffer(buf, i);
//     } else {
//       Serial.println(" No se recibieron bytes en lectura de bloque.");
//     }
//   } else {
//     Serial.println(" Se omitió lectura de bloque por error en endTransmission.");
//   }

//   // Explicación resumida por dirección
//   Serial.println("\nResumen diagnóstico (sugerencias):");
//   // Si hubo ACK detectado / endTransmission 0 en alguno de los intentos mostramos sugerencias distintas
//   // Para hacerlo simple: comprobamos quick ack con endTransmission tras enviar nada
//   Wire.beginTransmission(addr);
//   uint8_t quick = Wire.endTransmission();
//   }

// void setup() {
//   Serial.begin(115200);
//   delay(200);
//   Serial.println("\nDirect WHO_AM_I probe (diagnóstico directo para 0x68 y 0x7E)");
//   Wire.begin(SDA_PIN, SCL_PIN);
//   Wire.setClock(400000); // probar 400k y luego 100k si querés
//   delay(50);

//   probeAddress(ADDR_A);
//   probeAddress(ADDR_B);

//   Serial.println("\n--- FIN PROBE ---\n");
// }

// void loop() {
//   // Ejecuta el probe cada 6s para que veas cambios si cambiás pines/jumpers
//   delay(6000);
//   probeAddress(ADDR_A);
//   probeAddress(ADDR_B);
// }
