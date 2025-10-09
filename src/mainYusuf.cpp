// #include <DFRobot_ICM42688.h>
// #include <Arduino.h>

// // Dirección I2C: SDO pull down = 0x68 (por defecto), pull up = 0x69
// #define DFRobot_ICM42688_I2C_L_ADDR 0x68
// DFRobot_ICM42688_I2C ICM42688(DFRobot_ICM42688_I2C_L_ADDR);

// void setup() {
//   Serial.begin(115200);
//   while (ICM42688.begin() != 0) {
//     delay(1000);
//   }

//   // Configurar acelerómetro: ODR 1 kHz, ±16g (FSR_0)
//   ICM42688.setODRAndFSR(ACCEL, ODR_1KHZ, FSR_0);
//   ICM42688.startAccelMeasure(LN_MODE);
//   ICM42688.startFIFOMode();  // Modo FIFO (no estrictamente necesario para lecturas puntuales)
// }

// void loop() {
//   // Leer datos del FIFO (actualiza valores internos)
//   ICM42688.getFIFOData();

//   // Obtener valores del acelerómetro (en mg)
//   float accelDataX = ICM42688.getAccelDataX();
//   float accelDataY = ICM42688.getAccelDataY();
//   float accelDataZ = ICM42688.getAccelDataZ();

//   // Timestamp (microsegundos desde inicio)
//   unsigned long timestamp = micros();

//   // Enviar por Serial: timestamp, ax, ay, az separados por comas
//   Serial.print(timestamp);
//   Serial.print(',');
//   Serial.print(accelDataX);
//   Serial.print(',');
//   Serial.print(accelDataY);
//   Serial.print(',');
//   Serial.println(accelDataZ);

//   delay(1000); // Frecuencia de muestreo: 1 segundo (ajustable)
// }
