// // src/arduino_wrapper.cpp
// #include <Arduino.h>

// extern "C" {
//   #include "icm-42688.h"
//   #include "i2c_driver.h"

//   // stubs (definidos en esp32_stubs.cpp)
//   void SystemClock_Config(void);
//   void MX_GPIO_Init(void);
//   void MX_I2C1_Init(void);
//   void MX_SPI1_Init(void);

//   // globals (definidas en globals.cpp)
//   extern icm42688_t imu_sensor;
//   extern icm42688_data_t sensor_data;
//   extern int init_result;

//   // Asegurarse de que estas funciones C estén visibles
//   int i2c_driver_init_i2c1(uint8_t device_addr);
//   int i2c_driver_init_i2c2(uint8_t device_addr);
//   int i2c_read_wrapper(uint8_t reg, uint8_t *data, uint16_t len);
//   int i2c_write_wrapper(uint8_t reg, uint8_t *data, uint16_t len);
//   int icm42688_init(icm42688_t *dev);
//   int icm42688_read_all(icm42688_t *dev, icm42688_data_t *data);
// }

// void printInitErrorDetail(int rc) {
//   // Mapea códigos de error conocidos de icm42688_init (según implementación)
//   if (rc == 0) {
//     Serial.println("[INIT] Sensor inicializado correctamente.");
//     return;
//   }
//   Serial.print("[INIT][ERROR] código: ");
//   Serial.println(rc);
//   switch (rc) {
//     case -1:
//       Serial.println("  - Parametro inválido (puntero NULL).");
//       break;
//     case -2:
//       Serial.println("  - Falló lectura I2C al leer WHO_AM_I (posible conexión I2C).");
//       break;
//     case -3:
//       Serial.println("  - WHO_AM_I no coincide (producto distinto o dirección incorrecta).");
//       break;
//     case -4:
//       Serial.println("  - Falló escritura en registro (problema en bus o permisos).");
//       break;
//     default:
//       Serial.println("  - Código desconocido. Consulta el driver icm-42688.c para más info.");
//       break;
//   }
// }

// void setup() {
//   // Inicializa serial lo antes posible para ver mensajes
//   Serial.begin(115200);
//   delay(100);
//   Serial.println("=== BOOT: inicio diagnóstico ICM-42688 ===");

//   // Inicializaciones (stubs vacíos en esp32_stubs.cpp)
//   SystemClock_Config();
//   MX_GPIO_Init();
//   MX_I2C1_Init();
//   MX_SPI1_Init();

//   // Inicializar driver I2C (ESP driver) y comprobar resultado
//   int rc = i2c_driver_init_i2c1((uint8_t)(0x68 << 1));
//   if (rc != 0) {
//     Serial.print("[I2C] i2c_driver_init_i2c1 fallo, codigo: ");
//     Serial.println(rc);
//     Serial.println("Revisa wiring, SDA/SCL y alimentación del sensor.");
//   } else {
//     Serial.println("[I2C] driver I2C inicializado OK.");
//   }

//   // Intento de leer WHO_AM_I directamente para diagnóstico
//   uint8_t who = 0x00;
//   int rc_read = i2c_read_wrapper(WHO_AM_I_REG, &who, 1);
//   if (rc_read == 0) {
//     Serial.print("[I2C] WHO_AM_I leido OK: 0x");
//     if (who < 0x10) Serial.print('0');
//     Serial.println(who, HEX);
//   } else {
//     Serial.print("[I2C] Fallo leyendo WHO_AM_I, codigo: ");
//     Serial.println(rc_read);
//     Serial.println("  -> Asegurate de la direccion I2C (7/8-bit) y que sensor tiene alimentacion.");
//   }

//   // Configurar callbacks del bus en la estructura del sensor
//   imu_sensor.bus.read = i2c_read_wrapper;
//   imu_sensor.bus.write = i2c_write_wrapper;

//   // Inicializar sensor mediante la función del driver y mostrar resultado
//   init_result = icm42688_init(&imu_sensor);
//   printInitErrorDetail(init_result);

//   // Si hubo error de WHO_AM_I, imprimir sugerencia
//   if (init_result == -3) {
//     Serial.println("  Sugerencia: revisa que el sensor sea ICM-42688 (WHO_AM_I esperado: 0x47).");
//   }

//   Serial.println("=== Diagnóstico inicial completo ===");
// }

// void loop() {
//   // Si sensor no inicializó correctamente, intentar reintentar cada 2s
//   if (init_result != 0) {
//     static uint32_t last_retry = 0;
//     uint32_t now = millis();
//     if (now - last_retry > 2000) {
//       Serial.println("[LOOP] Reintentando inicializar sensor...");
//       init_result = icm42688_init(&imu_sensor);
//       printInitErrorDetail(init_result);
//       last_retry = now;
//     }
//     delay(10);
//     return;
//   }

//   // Leer todos los datos
//   int rc = icm42688_read_all(&imu_sensor, &sensor_data);
//   if (rc != 0) {
//     Serial.print("[READ] icm42688_read_all fallo, codigo: ");
//     Serial.println(rc);
//     Serial.println("  -> Comprueba conexion I2C y pull-ups. Reintentando en 200ms.");
//     delay(200);
//     return;
//   }

//   // Imprimir valores (brutos)
//   Serial.print("[DATA] TEMP: ");
//   Serial.print(sensor_data.temp);
//   Serial.print(" | ACC: ");
//   Serial.print(sensor_data.accel_x);
//   Serial.print(", ");
//   Serial.print(sensor_data.accel_y);
//   Serial.print(", ");
//   Serial.print(sensor_data.accel_z);
//   Serial.print(" | GYRO: ");
//   Serial.print(sensor_data.gyro_x);
//   Serial.print(", ");
//   Serial.print(sensor_data.gyro_y);
//   Serial.print(", ");
//   Serial.println(sensor_data.gyro_z);

//   delay(100);
// }
