#ifndef DOLL_SENSORS_H
#define DOLL_SENSORS_H

#include <Wire.h>

struct SensorSnapshot {
  bool temperatureValid = false;
  float temperatureC = 0;
  float humidityPercent = 0;
  bool batteryValid = false;
  float batteryVoltage = 0;
  int batteryPercent = 0;
};

static uint8_t shtCrc8(const uint8_t* data, size_t length) {
  uint8_t crc = 0xFF;
  for (size_t i = 0; i < length; i++) {
    crc ^= data[i];
    for (uint8_t bit = 0; bit < 8; bit++)
      crc = (crc & 0x80) ? (crc << 1) ^ 0x31 : crc << 1;
  }
  return crc;
}

static void sensorsInit() {
#if ENABLE_SHT3X
  Wire.begin(SENSOR_SDA, SENSOR_SCL);
  Wire.setClock(100000);
  Serial.println("[SENSOR] SHT3X I2C initialized");
#endif
#if ENABLE_BATTERY_MONITOR
  analogReadResolution(12);
  analogSetPinAttenuation(BATTERY_ADC_PIN, ADC_11db);
  Serial.println("[SENSOR] battery ADC initialized");
#endif
}

static bool readSht3x(SensorSnapshot& snapshot) {
#if ENABLE_SHT3X
  Wire.beginTransmission(SHT3X_ADDRESS);
  Wire.write(0x24);
  Wire.write(0x00);
  if (Wire.endTransmission() != 0) return false;
  delay(16);
  if (Wire.requestFrom(SHT3X_ADDRESS, 6) != 6) return false;
  uint8_t data[6];
  for (uint8_t i = 0; i < 6; i++) data[i] = Wire.read();
  if (shtCrc8(data, 2) != data[2] || shtCrc8(data + 3, 2) != data[5])
    return false;
  uint16_t rawTemperature = ((uint16_t)data[0] << 8) | data[1];
  uint16_t rawHumidity = ((uint16_t)data[3] << 8) | data[4];
  snapshot.temperatureC = -45.0f + 175.0f * rawTemperature / 65535.0f;
  snapshot.humidityPercent = 100.0f * rawHumidity / 65535.0f;
  snapshot.temperatureValid = true;
  return true;
#else
  return false;
#endif
}

static void readBattery(SensorSnapshot& snapshot) {
#if ENABLE_BATTERY_MONITOR
  uint32_t millivolts = analogReadMilliVolts(BATTERY_ADC_PIN);
  snapshot.batteryVoltage = millivolts * BATTERY_DIVIDER_RATIO / 1000.0f;
  snapshot.batteryPercent = constrain(
      (int)((snapshot.batteryVoltage - 3.20f) * 100.0f / 1.0f), 0, 100);
  snapshot.batteryValid = true;
#endif
}

static String sensorSnapshotJson() {
  SensorSnapshot snapshot;
  readSht3x(snapshot);
  readBattery(snapshot);
  String json = "{";
  json += "\"temperature_c\":";
  json += snapshot.temperatureValid ? String(snapshot.temperatureC, 1) : "null";
  json += ",\"humidity_percent\":";
  json += snapshot.temperatureValid ? String(snapshot.humidityPercent, 1) : "null";
  json += ",\"battery_voltage\":";
  json += snapshot.batteryValid ? String(snapshot.batteryVoltage, 2) : "null";
  json += ",\"battery_percent\":";
  json += snapshot.batteryValid ? String(snapshot.batteryPercent) : "null";
  json += "}";
  return json;
}

#endif
