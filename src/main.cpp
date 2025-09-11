#include <Arduino.h>
#include <SPI.h>
#include "ICM42688.h"

#define MAX_SAMPLES 128
#define INT_PIN 5

// I2C bus y frecuencia
ICM42688_FIFO imu(Wire, 0x69);//, 8000000); // SPI, CS pin, velocidad alta (8 MHz)

float ax[MAX_SAMPLES], ay[MAX_SAMPLES], az[MAX_SAMPLES];
float gx[MAX_SAMPLES], gy[MAX_SAMPLES], gz[MAX_SAMPLES];

const float SENSOR_ODR_HZ = 12.5f; //1000.0;  // Tasa de muestreo esperada (en Hz) const float SENSOR_ODR_HZ = 12.5f;  // <-- Debe coincidir con la ODR configurada en el sensor
uint32_t lastReadMs = 0;
const uint32_t readIntervalMs = 50; // Intervalo de lectura (cada 50 ms)
volatile bool dataReady = false;

void setup() {
  Serial.begin(115200);
  while (!Serial);
  Wire.begin();
  pinMode(INT_PIN, INPUT_PULLUP);

 // Inicializar sensor
  if(imu.begin() < 0) 
  {
    Serial.println("ERROR iniciando ICM-42688");
    while (1) { delay(1000); }
  }
  Serial.println("ICM-42688 iniciado correctamente.");

  // Rangos de operación más sensibles // ±2g y ±250 °/s
  if(imu.setAccelFS(ICM42688::gpm2) < 0)
  {
    Serial.println("ERROR iniciando la sensibilidad del acelerometro ICM-42688");
    while (1) { delay(1000); }
  }    
  if(imu.setGyroFS(ICM42688::dps250) < 0)  
  {
    Serial.println("ERROR iniciando la sensibilidad del giroscopio ICM-42688");
    while (1) { delay(1000); }
  }  

  // set output data rate to 12.5 Hz
	imu.setAccelODR(ICM42688::odr12_5);
	imu.setGyroODR(ICM42688::odr12_5);

  // Configurar FIFO (acelerómetro + giroscopio)
  if(imu.enableFifo(true, true, false) < 0)
  {
    Serial.println("ERROR activando la configuracion FIFO del ICM-42688");
    while (1) { delay(1000); }
  }  
  if(imu.enableDataReadyInterrupt() < 0)
  {
    Serial.println("ERROR iniciando el INT data-ready");
    while (1) { delay(1000); }
  }  

  attachInterrupt(digitalPinToInterrupt(INT_PIN), onDataReady, RISING);

  Serial.println("FIFO activado; INT data-ready también.");
}

void IRAM_ATTR onDataReady() 
{
  dataReady = true;
}

void loop() {
  uint32_t now = millis();

  if (now - lastReadMs >= readIntervalMs || dataReady) 
  {
    dataReady = false;
    lastReadMs = now;

    // read del sensor
    imu.getAGT();

    if (imu.readFifo() < 0) {
      Serial.println("Overflow o error en FIFO. Reseteando...");
      delay(5);
      imu.enableFifo(true, true, false);
      return;
    }

    size_t n = 0;
    imu.getFifoAccelX_mss(&n, nullptr);
    if (n == 0) return;  // Si no hay muestras, salto

    // limitamos lectura a MAX_SAMPLES
    size_t toRead = min(n, (size_t)MAX_SAMPLES);

    //float ax[n], ay[n], az[n], gx[n], gy[n], gz[n];

    size_t nAccel = toRead;
    imu.getFifoAccelX_mss(&nAccel, ax);
    imu.getFifoAccelY_mss(&nAccel, ay);
    imu.getFifoAccelZ_mss(&nAccel, az);

    size_t nGyro = 0;
    imu.getFifoGyroX(&nGyro, gx);
    imu.getFifoGyroY(&nGyro, gy);
    imu.getFifoGyroZ(&nGyro, gz);

    size_t nValido = min(nAccel, nGyro); // número de muestras coherente

    unsigned long t_read = micros();
    double dt_us = 1000000.0 / SENSOR_ODR_HZ; // debe coincidir con ODR

    for (size_t i = 0; i < nValido; ++i) {
      unsigned long ts = (unsigned long)(t_read - (unsigned long)((nValido - 1 - i) * dt_us));
      Serial.printf("%lu,%.6f,%.6f,%.6f,%.6f,%.6f,%.6f\n", ts, ax[i], ay[i], az[i], gx[i], gy[i], gz[i]);
    }
    // si había más muestras en el FIFO que MAX_SAMPLES, la próxima iteración las leerá
  }
  // pequeña pausa para que no consuma toda la CPU
  delay(1);
}

//comentario chupete chupeton