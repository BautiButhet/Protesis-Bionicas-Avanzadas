// src/i2c_driver_esp.cpp
#include <Wire.h>
#include <stdint.h>
#include "i2c_driver.h"

// Variables internas
static uint8_t g_device_addr_8bit = (0x68 << 1);

extern "C" {

int i2c_driver_init_i2c1(uint8_t device_addr) {
  g_device_addr_8bit = device_addr;
  Wire.begin();
  return 0;
}

int i2c_driver_init_i2c2(uint8_t device_addr) {
  g_device_addr_8bit = device_addr;
  Wire.begin();
  return 0;
}

int i2c_read_wrapper(uint8_t reg, uint8_t *data, uint16_t len) {
  uint8_t addr7 = g_device_addr_8bit >> 1; // Wire usa 7-bit address
  Wire.beginTransmission(addr7);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) { // repeated start
    return -1;
  }
  uint16_t received = Wire.requestFrom((uint8_t)addr7, (uint8_t)len);
  if (received < len) return -2;
  for (uint16_t i = 0; i < len; ++i) {
    if (Wire.available()) data[i] = Wire.read();
    else return -3;
  }
  return 0;
}

int i2c_write_wrapper(uint8_t reg, uint8_t *data, uint16_t len) {
  uint8_t addr7 = g_device_addr_8bit >> 1;
  Wire.beginTransmission(addr7);
  Wire.write(reg);
  Wire.write(data, len);
  return (Wire.endTransmission() == 0) ? 0 : -1;
}

} // extern "C"
