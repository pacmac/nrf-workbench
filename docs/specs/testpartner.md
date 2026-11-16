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
- **Announces a distinct USB product string**, `nrfwb-parking 1`, set through the core's
  `USBDevice.setProductDescriptor()` **[verified: it exists in `Adafruit_USBD_Device.h`]**. The USB
  vendor and product ids are unchanged (`239a:8029`), so the identity code still reads the state as
  `app`, and the serial (derived from the chip) is unchanged. Software can then verify **which image
  is running** from the device itself, a content check that does not depend on timing.
- **Never sleeps the system, never starts the radio, never writes flash, never prints.** It has no
  console output at all, so a silent port is the expected state.

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
  `nrfwb-parking 1` (the optional expected-product check in `programmer.md`), with the same serial.
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
  string `nrfwb-parking 1`, stays stable, and the touch re-enters the bootloader from it.
