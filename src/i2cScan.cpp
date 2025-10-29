// // conectar SDA gpio21
// // conectar SCL gpio22

// #include <Arduino.h>

// #include <Wire.h>
 
// // void setup() {
// //   Wire.begin();
// //   Serial.begin(115200);
// //   Serial.println("\nI2C Scanner");
// // }
 
// // void loop() {
// //   byte error, address;
// //   int nDevices;
// //   Serial.println("Escaneando puertos.");
// //   nDevices = 0;
// //   for(address = 1; address < 127; address++ ) {
// //     Wire.beginTransmission(address);
// //     error = Wire.endTransmission();
// //     if (error == 0) {
// //       Serial.print("I2C dispositivo encontrado en 0x");
// //       if (address<16) {
// //         Serial.print("0");
// //       }
// //       Serial.println(address,HEX);
// //       nDevices++;
// //     }
// //     else if (error==4) {
// //       Serial.print("Error en direccion 0x");
// //       if (address<16) {
// //         Serial.print("0");
// //       }
// //       Serial.println(address,HEX);
// //     }    
// //   }
// //   if (nDevices == 0) {
// //     Serial.println("No se encontraron dispositivos\n");
// //   }
// //   else {
// //     Serial.println("Terminado\n");
// //   }
// //   delay(5000);          
// // }



// ///////////////////////////////////////////////////////////////////


// #include <Arduino.h>
// #include <Wire.h>

// #define SDA_PIN 21
// #define SCL_PIN 22

// // Explicación de los errores de endTransmission
// const char* explainError(uint8_t e) {
//   switch (e) {
//     case 0: return "OK (ACK recibido)";
//     case 1: return "Buffer overflow (datos demasiado largos)";
//     case 2: return "NACK en direccion (sin dispositivo en esa direccion)";
//     case 3: return "NACK en datos";
//     case 4: return "Error desconocido (bus ocupado o timeout)";
//     default: return "Codigo desconocido";
//   }
// }

// void setup() {
//   Serial.begin(115200);
//   delay(500);
//   Serial.println();
//   Serial.println("=== ESCANEO I2C ===");
//   Serial.printf("Configurando SDA=%d, SCL=%d ...\n", SDA_PIN, SCL_PIN);

//   // Inicializar I2C
//   Wire.begin(SDA_PIN, SCL_PIN);
//   Wire.setClock(400000);
//   delay(200);

//   // Prueba básica: verificar si el bus responde
//   Serial.println("Verificando bus...");
//   Wire.beginTransmission(0x00);
//   uint8_t errTest = Wire.endTransmission();
//   if (errTest == 4) {
//     Serial.println("⚠️  Error: bus I2C no responde (posible desconexion o corto)");
//     Serial.println("Revisar SDA/SCL, GND común, y resistencias pull-up.");
//   } else {
//     Serial.println("✅ Bus I2C activo. Iniciando escaneo...");
//   }
//   Serial.println();

//   int found = 0;

//   for (uint8_t addr = 1; addr < 127; addr++) {
//     Wire.beginTransmission(addr);
//     uint8_t error = Wire.endTransmission();

//     if (error == 0) {
//       Serial.printf("✔ Dispositivo encontrado en 0x%02X\n", addr);
//       found++;
//     } else if (error != 2) {
//       Serial.printf("⚠ Direccion 0x%02X -> error %d (%s)\n",
//                     addr, error, explainError(error));
//     }
//   }

//   if (found == 0) {
//     Serial.println("\n❌ No se detectaron dispositivos I2C.");
//     Serial.println("Verificar:");
//     Serial.println(" - Alimentación (3.3V y GND compartidos con ESP32)");
//     Serial.println(" - CS (si existe) en HIGH para habilitar modo I2C");
//     Serial.println(" - Pull-ups de 4.7kΩ en SDA y SCL a 3.3V");
//     Serial.println(" - AD0/SAO a GND → dirección 0x68, o a 3.3V → 0x69");
//     Serial.println(" - Revisar continuidad de cables y conexión firme");
//   } else {
//     Serial.printf("\n✅ Escaneo finalizado: %d dispositivos encontrados.\n", found);
//   }

//   Serial.println("=======================");
// }

// void loop() {
//   // No hace falta repetir
// }
