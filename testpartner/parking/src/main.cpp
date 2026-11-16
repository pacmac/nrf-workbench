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
// The one exception is the separate `pinscan` env (-DPINSCAN, below), which exists to find which pin
// the debug dongle is wired to; it announces a different product string.
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

#ifdef PINSCAN
// ---- pinscan: find which RAK pin the debug dongle is wired to (docs/specs/testpartner.md) ----
// In turn, each candidate pin sends "NRFWB-PIN <n>\r\n" at 115200 8N1, then goes back to a
// high-impedance input, so a pin that is wired to the dongle's TX is never driven against it.
// 21 and 33 are the WisBlock IO3 and switch pins from memory of the RAK variant, NOT from this
// repository's variant.h: unverified. gpio 6 and 8 go to the on-board debug chip, not to a wire.
static const uint8_t CANDIDATES[] = {17, 34, 21, 4, 9, 10, 16, 15, 31, 5, 13, 14, 33};
static const uint32_t BAUD = 115200;
static const uint32_t CPU_HZ = 64000000UL;

static void cycles_wait_until(uint32_t target) {
  while ((int32_t)(DWT->CYCCNT - target) < 0) { }
}

// One byte, 8N1, LSB first. Bit i starts at round(i * CPU_HZ / BAUD) cycles after the start edge,
// so the rounding never accumulates. Interrupts are masked for the ~87 us this takes.
static void put_byte(uint8_t pin, uint8_t b) {
  __disable_irq();
  const uint32_t t0 = DWT->CYCCNT;
  digitalWrite(pin, LOW);                                    // start bit
  for (uint8_t i = 0; i < 8; i++) {
    cycles_wait_until(t0 + (uint32_t)(((uint64_t)(i + 1) * CPU_HZ + BAUD / 2) / BAUD));
    digitalWrite(pin, (b >> i) & 1 ? HIGH : LOW);
  }
  cycles_wait_until(t0 + (uint32_t)(((uint64_t)9 * CPU_HZ + BAUD / 2) / BAUD));
  digitalWrite(pin, HIGH);                                   // stop bit
  cycles_wait_until(t0 + (uint32_t)(((uint64_t)10 * CPU_HZ + BAUD / 2) / BAUD));
  __enable_irq();
}

static void announce(uint8_t pin) {
  char msg[24];
  const int n = snprintf(msg, sizeof msg, "NRFWB-PIN %u\r\n", (unsigned)pin);
  digitalWrite(pin, HIGH);        // line idle, set before the pin is driven
  pinMode(pin, OUTPUT);
  delay(2);
  for (int i = 0; i < n; i++) put_byte(pin, (uint8_t)msg[i]);
  delay(2);
  pinMode(pin, INPUT);            // never leave a candidate driving
}

static void pinscan_round() {
  static bool counter_on = false;
  if (!counter_on) {              // the cycle counter, enabled once
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CYCCNT = 0;
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
    counter_on = true;
  }
  for (size_t k = 0; k < sizeof CANDIDATES; k++) {
    for (int rep = 0; rep < 2; rep++) { announce(CANDIDATES[k]); delay(20); }
  }
}
#endif

void loop() {
  const uint32_t t = millis();
#ifdef PINSCAN
  static uint32_t last_scan = 0;
  if (t - last_scan >= 5000) { last_scan = t; pinscan_round(); }   // every 5 s, ~0.4 s long
#endif

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
