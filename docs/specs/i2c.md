# I2C master on the host's bus (DRAFT)

Peter, 2026-11-18: "the I2C is needed and is a real test case for this workbench". The `device` session wants the bench Pi to
talk I2C to a TimerCAM X (Grove G4 = SDA, G13 = SCL) to develop its I2C-slave transport. Today the Pi has no `/dev/i2c-*`, no
I2C command, and the Wiring page's I2C intent is "not built". Tags: **[stated]**, **[derived]**, **[unknown]** as elsewhere.

## GAP defects first

1. **The Pi's I2C is off.** `/boot/firmware/config.txt` has no `dtparam=i2c_arm=on`, so there is no `/dev/i2c-1`. Enabling it is a
   boot-config change and a reboot. The installer never edits boot configuration (install.md); this is a person's step, said
   out loud, and `i2c.buses` says so when it finds no bus. **[verified on the Pi, 2026-11-18]**
2. **The I2C pins belong to the kernel once it is on.** BCM 2 and 3 (physical 3 and 5) are the SoC's I2C1 and carry the Pi's 1.8 kΩ
   pull-ups to 3.3 V. The GPIO pin map must never be given a driver that takes them as plain GPIO (gpio.md); `gpio.assign` may
   record them with service `i2c`, and that record is this spec's gate. **[derived]**
3. **The bus speed is not a per-call setting.** It is the device tree's `clock-frequency` (default 100 kHz), changed by
   `dtparam=i2c_arm_baudrate=` and a reboot. `i2c.buses` reports what the kernel says and does not claim to set it. **[derived]**
4. **A scan can disturb a device.** i2cdetect's probes (a quick write, a one-byte read) have side effects on some parts (a relay
   expander, an EEPROM pointer). So `i2c.scan` has **no default range**: the caller names the first and last address. **[derived]**

## Commands (area `i2c`, capability `i2c` = the `gpio` capability: a pin-map folder on a Raspberry Pi)

- `i2c.buses()` reads `/dev/i2c-*`: `[{bus, node, name, clock_hz}]`, and when there is none, why (`i2c_enabled: false` with the line to
  add to `config.txt`). Opens nothing.
- `i2c.transfer(bus, address, write=hex, read=N)` is **one atomic transaction** (`I2C_RDWR`): a write of the bytes, then, with a
  repeated start, a read of N bytes, the kernel holding the bus for both. Either part may be left out, not both. `address` is the 7-bit
  address, 0x08 to 0x77 (the rest are reserved by the I2C spec and are a usage error). `write` is a hex string (`"0a ff"`, spaces and
  `0x` allowed), at most 256 bytes; `read` is 0 to 256. Returns `written`, `read` (hex), `bytes`, `duration_s`.
- `i2c.scan(bus, first, last)` tries each address in the range with a one-byte read and returns those that acknowledged.

**The gate.** A transfer or scan on bus 1 is refused `i2c_pins_unclaimed` unless the pin map holds assignments for BCM 2 **and** BCM 3
with service `i2c` and `voltage_verified: true`. That is the pin-claim rule for pins, applied to a bus: the bench does not talk
on a bus nobody has said the voltage of. A bus with no known pins is refused the same way. **[derived from "sending needs measured voltage"]**

**Outcomes.** The device not acknowledging (`ENXIO`/`EREMOTEIO`) is `no_reply`, failed, with the address and which part (write or
read) it was; it is a result, not an error. A missing `/dev/i2c-N` is `node_absent`. The kernel's transfer timeout is set to 200 ms
(`I2C_TIMEOUT`, in 10 ms units): the longest transfer allowed is 256 + 256 bytes at 100 kHz, 512 x 9 bits / 100 kHz = 46 ms, so
200 ms is 4x that and still catches a slave that stretches the clock for ever. One attempt; nothing retries.

## Measured on the bench Pi (2026-11-18)

I2C was turned on with `dtparam i2c_arm=on` and `modprobe i2c-dev` at runtime (no reboot needed), then made permanent by
uncommenting `dtparam=i2c_arm=on` in `/boot/firmware/config.txt` (backup `config.txt.bak-before-i2c-20261118`) and adding `i2c-dev`
to `/etc/modules`. **The persistent part has not been through a reboot.** `i2c.buses` then listed bus 1 (`bcm2835 (i2c@7e804000)`,
**100,000 Hz** from the device tree) and buses 20 and 21 (the HDMI DDC channels, `fef04500.i2c`, `fef09500.i2c`), and a transfer
on bus 1 was refused `i2c_pins_unclaimed` with BCM 2 and 3 named: the gate works on the real service.

A miss the Pi caught: the first version read bus names from `/sys/class/i2c-adapter`, which the fake tree had and the Pi does not; the
real place is `/sys/class/i2c-dev/i2c-N`.

**Not done, and why:** no real transfer. The gate needs BCM 2 and 3 assigned with service `i2c` and a **measured** voltage, and
nobody has measured the TimerCAM's SDA and SCL idle level (Peter's "3.3 V" is a stated value, not a measurement). The bench does not
claim a measurement for a person.

## Files this spec allows

`nrf_workbench/i2c/{__init__,bus,commands}.py`, `nrf_workbench/service.py` (registration), `nrf_workbench/core/status.py` and
`docs/specs/core.md` (`i2c_pins_unclaimed`), `docs/specs/{i2c,gpio,api}.md`, `tests/i2c/`.
