# Testpartner — the parking image (DRAFT, not agreed)

Area `testpartner` (firmware, proved on hardware). Peter, 2026-11-16: "it would be useful to be able
to get a device out of OTA [the bootloader] without having it run some firmware which might put it to
sleep anyway", and, asked whether a do-nothing image could flash the LEDs: "it can flash the LEDs
can't it?" This spec covers the first testpartner image, **the parking image**. The fuller DUT-side
test firmware in the brief (GPIO, buses, SAADC, System-OFF wake) comes later and is not specified here.

Tags: **[stated]** Peter said it; **[verified]** read in a source or run; **[unknown]** not looked at.

## Files this spec allows

`testpartner/parking/platformio.ini`, `testpartner/parking/src/main.cpp`,
`testpartner/parking/README.md`, and an ignore line for its build directory in `.gitignore`.
Nothing is vendored from another repository (see Dependencies).

## GAP defects first

1. **A truly empty image cannot be a parking image.** With no USB stack it would not enumerate, so
   the identity code could not see it, and it could not be touched again to re-enter the bootloader.
   It would swap "stranded in the bootloader" for "stranded and invisible". So it is a real minimal
   application: USB CDC kept up, LEDs blinking.
2. **A parking image is something a firmware can do that a running firmware like mt-alarm may not:**
   put the radio and CPU to sleep and stop answering (the prior art's "silence is not death"). The
   parking image never sleeps the system, never touches the radio, and does nothing the bench must
   wait for.
3. **Flashing it replaces whatever the unit ran**, and the previous image cannot be read back over
   USB. Restoring needs the original package (for the RAK on this bench, the `mt-alarm` v3
   `firmware.zip`, which Peter named). The first flash onto a unit is therefore Peter's decision.
4. **The board and variant definitions are not in this repository, deliberately.** The core bundled
   with PlatformIO has no RAK4631 board or variant; the `mt-alarm` project vendors both. That
   project has **no git remote** (it is local only) and the variant files carry an LGPL header from
   other authors, so copying them here would publish a private repository's content. The build
   reads them from a directory the builder names (`NRFWB_RAK_DIR`).

## TOOL what the parking image does

- **Keeps USB CDC enumerated**, so the host sees it and a touch re-enters the bootloader. The
  Arduino core calls `enterSerialDfu()` from its TinyUSB layer when the touch arrives
  **[verified: `Adafruit_TinyUSB_nrf.cpp`]**, so any application built with it is re-enterable.
- **Blinks both LEDs as a human-visible "the application is running" signal.** Green (pin 35) is a
  heartbeat; blue (pin 36) is a recognisable pattern that is not the heartbeat, so the parking image
  can be told from a firmware that merely lights an LED. Both pins are active high **[verified: the
  RAK variant, `LED_STATE_ON 1`]**. The patterns are for a human or a camera, not data.
- **Announces a distinct USB product string**, `nrfwb-parking 2` (the number is bumped whenever the
  image's behaviour changes, so a flash of the new image is told from the old one), set at compile
  time by `board_build.usb_product` **[verified: the string is in the binary and the board reports it]**,
  not at run time, because the core starts USB before `setup()`. (An earlier draft of this line named
  `USBDevice.setProductDescriptor()`; that was not what was built.) The USB
  vendor and product ids are unchanged (`239a:8029`), so the identity code still reads the state as
  `app`, and the serial (derived from the chip) is unchanged. Software can then verify **which image
  is running** from the device itself, a content check that does not depend on timing.
- **Never sleeps the system, never starts the radio, never writes flash.** Its USB CDC is silent.
- **Has a debug console on the core's `Serial2`, gpio20 TX and gpio19 RX** (the RAK19003's UART0 pair,
  connector pins 11 and 12, `TXD0`/`RXD0`; the RAK4631 module table gives them as P0.20 and P0.19). It
  prints `NRFWB-PARKING 2 up=<ms>` each second and echoes each byte received as `ECHO 0x<hex>`, so both
  dongle wires can be proved. **Proved on the bench, 2026-11-16:** 8 of 8 heartbeats in 8 s at about 1.008 s
  apart on the dongle, and `ping\n` sent from the dongle came back as `ECHO 0x70 0x69 0x6E 0x67 0x0A`.
  Peter, 2026-11-16: the parking image is "MUCH more useful for debugging and development of this
  workbench" than mt-alarm, so **it is the image the bench RAK is left running**.

## TOOL the pinscan image (second env of the same project)

Peter, 2026-11-16: "you could also listen on the serial dongle?" then "the serial dongle might not
work on the mt-alarm, because ... the mt-alarm uses different pin for serial debugging. but you could
test that on the parking firmware?" A 150 s capture of the dongle with mt-alarm running returned
nothing **[verified]**, which does not say whether the wire, the dongle or the pin is at fault.

- **Which pin the dongle is wired to is not known** (the prior art's note says gpio17 out and gpio31 in
  for mt-alarm; Peter doubts it for this bench). It is not guessed.
- **Env `pinscan`** is the parking image plus a debug banner: in turn on each candidate edge pin it
  sends `NRFWB-PIN <n>` at 115200 8N1 (the dongle tool's fixed rate), then returns that pin to a
  high-impedance input. Reading the dongle shows which pin number arrives. It announces its own
  product string, `nrfwb-pinscan 1`, so a flash of it is verified like any other.
- **Candidates:** the pins the board variant says reach an edge or a UART (gpio 4, 5, 9, 10, 13, 14,
  15, 16, 17, 31, 34) plus 21 and 33, which are the WisBlock IO3 and switch pins **from memory of the
  RAK variant, not from this repository: [unknown], marked as such in the source**. gpio 6 and 8 go to
  the on-board debug chip, not to a wire, and are excluded.
- **Why bit-banged:** the core builds `Serial2` for one fixed pin pair at compile time. The line is
  written from the cycle counter (exact to a cycle, 64 MHz) with interrupts masked for the ~87 us of a
  byte. Non-active pins are inputs, so a pin that is wired to the dongle's TX is never driven against it.
- **Research, 2026-11-16 (Peter named the base: RAK19003 WisBlock Mini Base Board; both RAK datasheet
  pages read):** the RAK19003 header **J7 is `1 RX1 | 2 TX1 | 3 GND | 4 BOOT`**; its 40-pin core
  connector carries the UART pair on connector pins 33 and 34. The RAK4631 page names connector pins
  33/34 `UART2_TX`/`UART2_RX`, which is nRF **P0.16 (TX) and P0.15 (RX)**, and this repository's
  variant names the same two pins `PIN_SERIAL1_TX 16`/`PIN_SERIAL1_RX 15` (the core's `Serial1`). **The two
  datasheets disagree on the UART numbers** (the base calls those connector pins UART1, the module calls
  them UART2) and the base page gives **no direction** for the J7 labels and labels connector pin 33
  `TXD1` yet describes it "RX". So the pins are firm (gpio16 out, gpio15 in) and which J7 hole each lands on
  is not stated. Both were in the first scan and nothing was heard, so the bit-banged timing is the next
  thing to remove: pinscan now also sends on the core's hardware `Serial1` (gpio16).
- **RESOLVED, 2026-11-16:** Peter said the dongle is wired to `tx0`/`rx0`. I had assumed the J7 header
  (UART1, connector pins 33/34), which was wrong: his wires are on UART0 (connector pins 11/12), which
  the RAK4631 module table gives as **gpio20 TX, gpio19 RX**, and neither pin was in the two earlier
  scans. With them added, pinscan was heard at once: `NRFWB-PIN 20` 8 times in 20 s (112 bytes, all clean,
  which also validates the bit-banged timing). The two negative runs below are therefore explained by the
  pin list, not by timing or ground.
- **Result of the hardware-UART run, 2026-11-16 [verified on the bench]:** pinscan with the core's
  `Serial1` sending `NRFWB-HWUART1 TX=16` was flashed (4.654 s, 113 of 113 marks, product confirmed) and
  the dongle read for 20 s (four rounds): **nothing received again**. Two negative runs on the J7 pair,
  one bit-banged and one hardware, so timing is no longer the likeliest cause. What is left is physical
  and not visible from software: the dongle's RX is not on the RAK's gpio16 net, or the ground is not
  common, or the two signal wires are crossed. The unit was restored to the mt-alarm package afterwards.
- **Receive side (dongle TX to a RAK pin) is a later step**; this finds the transmit pin only.
- **Result, 2026-11-16 [verified on the bench]:** pinscan flashed (4.428 s, 108 of 108 marks, product
  string confirmed), the dongle read for 30 s (six scan rounds) and **received nothing from any of the
  13 candidates**. The dongle itself is sound (enumerates as `cdc_acm`, opened by its by-id name). So
  the dongle's RX is not on any listed pin, or the common ground is missing, or the scan's bit timing
  is wrong **[unknown which]**. The next input is Peter's: which RAK pins the two dongle wires and the
  ground actually go to. The unit was restored to the mt-alarm package afterwards (21.539 s, 649 of 649
  marks, board-default product string confirmed).

## TOOL dependencies and build

- **Board and variant:** `NRFWB_RAK_DIR` names a directory containing `boards/wiscore_rak4631.json`
  and `variants/WisCore_RAK4631_Board/`. On Peter's dev host that is the `mt-alarm` project's
  `device/rak` directory. The file is read, never edited.
- **Toolchain:** the installed PlatformIO `nordicnrf52` platform and Adafruit nRF52 core, offline
  (present on the dev host **[verified]**). The build is a development-host task; a Pi only needs
  the resulting zip.
- **Output:** `firmware.zip`, a DFU package for S140 6.1.1 (`softdevice_req` 182), built into the
  project's own ignored `.pio/` directory. **The zip is a build artefact and is not committed**
  (the brief's principle: flash the artefact the build made).

## TOOL how it is verified

- **Software, no human:** after a flash, the returned device's product string must equal
  `nrfwb-parking 2` (the optional expected-product check in `programmer.md`), with the same serial.
  A parking image that is not the one running is `flash_suspect`.
- **Human, optional:** the two LED patterns are visible.
- **The cycle:** parking to bootloader to the original firmware and back, each step verified from
  USB identity, run on the RAK once Peter says go. It also yields repeated entry and flash
  measurements for the profile's baseline.

## DECIDE derived (Claude; Peter may overrule)

1. **A minimal USB application, not an empty one.** Constraint: GAP 1.
2. **A distinct product string.** Constraint: it gives a device-side, timing-independent way to
   verify what was written, which the flash verdict lacked (the first real flash showed a duration
   check cannot be trusted).
3. **Board and variant by environment variable, not vendored.** Constraint: GAP 4.
4. **The zip is not committed.** Constraint: a build artefact; committing binaries invites drift.

## VERIFY

- The package builds, is a zip with `manifest.json`, requires SoftDevice 182, and is small.
- The built image contains the product string.
- On the RAK (with Peter's go): it enumerates as `239a:8029` with the same serial and the product
  string `nrfwb-parking 2`, stays stable, and the touch re-enters the bootloader from it.
