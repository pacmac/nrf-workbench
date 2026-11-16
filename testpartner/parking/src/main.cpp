// parking: the minimal RAK4631 application. See docs/specs/testpartner.md.
//
// What it does, and all it does:
//   * keeps USB CDC enumerated (Serial.begin), so the host can see the board and a touch can put it
//     back into the bootloader (the Arduino core's TinyUSB layer handles the touch itself);
//   * blinks the two LEDs as a human-visible "the application is running" signal;
//   * announces the USB product string "nrfwb-parking 1" (set in platformio.ini, at compile time).
//
// What it deliberately never does: sleep the system, start the radio, write flash, or print. A
// silent port is the expected state. The LED patterns are for a human or a camera, not data.
#include <Arduino.h>
// Including this is what makes the build link the USB stack. Without it the compile succeeds and the
// LINK fails on `Serial` and Adafruit_USBD_CDC::begin (found by the first build): the library finder
// only pulls in a library that a source file names.
#include <Adafruit_TinyUSB.h>

// LED_GREEN is pin 35 and LED_BLUE pin 36, both active high (the RAK4631 variant).
//
// The patterns are chosen to be told apart by eye, not derived from a timing requirement:
//   green  a heartbeat: 100 ms on at the start of every second;
//   blue   two short pulses 300 ms apart, then a pause, repeating every 3 s, so it can never be
//          mistaken for the heartbeat, and the pair can be told from a firmware that merely lights
//          an LED.
static const uint32_t GREEN_PERIOD_MS = 1000;
static const uint32_t GREEN_ON_MS = 100;
static const uint32_t BLUE_PERIOD_MS = 3000;
static const uint32_t BLUE_ON_MS = 100;
static const uint32_t BLUE_SECOND_PULSE_AT_MS = 300;

void setup() {
  pinMode(LED_GREEN, OUTPUT);
  pinMode(LED_BLUE, OUTPUT);
  digitalWrite(LED_GREEN, LOW);
  digitalWrite(LED_BLUE, LOW);
  // USB CDC. Nothing is ever written to it; opening it is what keeps the device a normal,
  // touchable USB serial device.
  Serial.begin(115200);
}

void loop() {
  const uint32_t t = millis();

  const uint32_t g = t % GREEN_PERIOD_MS;
  digitalWrite(LED_GREEN, g < GREEN_ON_MS ? HIGH : LOW);

  const uint32_t b = t % BLUE_PERIOD_MS;
  const bool blue_on = (b < BLUE_ON_MS) ||
                       (b >= BLUE_SECOND_PULSE_AT_MS && b < BLUE_SECOND_PULSE_AT_MS + BLUE_ON_MS);
  digitalWrite(LED_BLUE, blue_on ? HIGH : LOW);

  // delay() yields to the other tasks, including the USB task. The LED edges are good to this
  // interval, which is far finer than anything an eye can tell apart.
  delay(10);
}
