# ESP32 — basic flash, reset, console and crash decoding (DRAFT)

Peter, 2026-11-18: "build the slices here, I dont want to have to run 2 PI's". The scope is deliberately
small: flash an image, reset a board and read its console, decode a crash. Not in scope here: OTA, Wi-Fi,
BLE, MQTT, portals, JTAG/GDB sessions. Tags: **[stated]** Peter said it; **[derived]** worked out from a
constraint; **[unknown]** nobody has measured it. No ESP32 board is attached to the bench Pi yet, so
everything below is proved against a fake `esptool` runner and the `esptool` CLI's own `--help`; the
real-board proof is listed as DEFERRED in the task.

## GAP defects first

1. **A USB id does not say what is behind a bridge chip.** A CP210x, CH340 or CH9102 is on ESP32 dev boards,
   and also on this bench's dongle, which is wired to a RAK4631. Matching a board profile by those ids would
   call the dongle an ESP32. So **a bridge-chip board is only an ESP32 because someone declared it** (a
   *target*). Only the native USB id `303a:1001` (the ESP32-S3/C3/C6 built-in USB Serial/JTAG) is recognised
   in the sense that `esp32.targets` lists it as `undeclared_native`; it is declared in one call like any
   other, because a flash names a target. **[derived]**
2. **The nRF interlock needs a unique USB serial, and bridge chips often have none.** A CP2102 commonly reports
   the same serial on every unit; a CH340 reports none. A target is therefore keyed by the adapter's **serial
   when it has one and no other attached device reports the same**, otherwise by its **slot** (the sysfs port
   name such as `1-1.3`, which is where it is plugged). A flash by a key that more than one attached device
   matches is refused (`serial_mismatch` is not enough: the refusal is `serial_unverifiable`). **[derived]**
3. **Opening an ESP32's serial port can reset it** when DTR/RTS reach EN and IO0, as they do on most dev
   boards. `console/port.py` never sets those lines but the kernel may raise them at open. Whether a given
   bridge resets the board at open is **[unknown]** until measured on hardware; the console therefore keeps
   its rule (`dtr_rts_reach_dut` true is refused) and an ESP32 target's console link is a separate, explicit
   choice (slice 2).
4. **A flash is judged by esptool's own verification, not by exit status alone**, as the nRF flash is judged by
   marks and not by exit status. esptool prints `Hash of data verified.` once per written part; the count
   must equal the number of parts, or the verdict is `flash_suspect`. **[derived]**

## Targets

`esp32.declare(name, adapter)`, `esp32.forget(name)`, `esp32.targets()`. A target is `{name, key, key_kind}`
where `key_kind` is `serial` or `slot`. The record is kept in `esp32.json` beside the wiring store
(`paths.wiring`), written atomically (`core/jsonstore`). `esp32.targets` lists each declared target with
whether its adapter is attached now, its node, and the same for undeclared `303a:1001` devices (shown with
`key_kind: native`). Nothing is probed by listing. **No command opens a port to ask a chip what it is except
`esp32.identify`, which is explicit.** **[derived: a probe toggles DTR/RTS, and an adapter wired to another
board's reset must never be toggled by a listing.]**

## Packages

Two kinds are accepted by `firmware.put` beside the nRF DFU zip, told apart from the bytes:

| kind | what it is | how it is recognised |
|---|---|---|
| `esp-image` | one whole-flash image (a merged or factory image) written at 0x0 | a partition table magic `AA 50` at byte 0x8000 and a length above 0x8002 |
| `esp-bundle` | a zip holding ESP-IDF's `flasher_args.json` and the files it names | `flasher_args.json` with a `flash_files` map of offset to path, each path in the zip |

An app-only `.bin` is refused with `bad_package` and the reason: it has no offset the store can know. A
bundle may carry the ELF named by `app_elf` (an optional key) for slice 3. The record gains `kind`, `parts`
(count), `chip` (from `extra_esptool_args.chip` when the bundle says so) and `elf` (a bool). On disk an
`esp-image` is `<sha>.bin`; everything else stays `<sha>.zip`, so existing stores are untouched.

## Flash

`esp32.flash(target, firmware, flash_bound_s, baud)` runs the same guards in the same order as
`programmer.flash`: bounds required; the stored file re-hashed against its id; the package must be an ESP
kind; one host lock; the target resolves to exactly one attached device; not protected; a node that joins by
device number; nobody holds the port; **one** attempt of `esptool --port NODE --baud B [--chip C]
write-flash ... OFFSET FILE ...`; the whole output returned, never a tail; the verdict per GAP 4. The caller
supplies `flash_bound_s` and derives it: `image_bytes x 10 / baud` is the wire time of the raw bytes, and a
compressed write is faster, so twice that plus the chip's connect and erase time is the ceiling the Firmware
page offers. A refused flash touched nothing. `esptool` is found next to this interpreter, else on `PATH`;
missing is `capability_missing`. After the write esptool hard-resets the board over RTS; the board then runs
the new image. **No return-path declaration is needed**: an ESP32 cannot be stranded the way an nRF in its
bootloader can, because download mode is left by the same reset.

## Reset and the console (slice 2)

`esp32.reset(target, mode)` with `mode` `run` or `bootloader`, over the adapter's DTR/RTS with the standard
auto-reset sequences, taking the host lock and refusing when the port is held. The console opens an ESP32
target's link with both lines left alone and records, per link, `dtr_rts_reach_dut: true` only through an
ESP32 target. **[unknown]**: the exact effect of the kernel's DTR/RTS raise at open, per bridge chip.

## Crash decoding (slice 3)

`esp32.decode(firmware, text)` finds `Backtrace:` lines (and the `PC`/`EXCVADDR` forms) in `text`, runs the
toolchain's `addr2line` against the ELF in the stored bundle, and returns each address with function, file
and line. The toolchain is found from `chip`; missing is `capability_missing` naming it. The console page
offers it on a selected block of lines.

## Files this spec allows

`nrf_workbench/esp32/{__init__,package,targets,flash,commands}.py`; `nrf_workbench/programmer/firmware_store.py`
and `nrf_workbench/guards/checks.py` (the two package kinds); `nrf_workbench/service.py` (registration, the
capability `esp32`); `docs/specs/{esp32,firmware,api,core}.md`; `tests/esp32/`; `tests/programmer/`; the UI
files named in the UI step; `scripts/verify_dashboard.py`.
