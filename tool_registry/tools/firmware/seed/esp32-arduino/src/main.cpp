// Seed: includes each installed library so the image build compiles and
// caches all of them. Not user firmware.
#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_NeoPixel.h>
#include <BH1750.h>

Adafruit_NeoPixel ring(12, D1, NEO_GRB + NEO_KHZ800);
BH1750 lux;

void setup() {
  Wire.begin();
  lux.begin();
  ring.begin();
}

void loop() {
  ring.fill(lux.readLightLevel() < 10 ? ring.Color(255, 160, 60) : 0);
  ring.show();
  delay(100);
}
