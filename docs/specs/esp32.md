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

**Built.** `esp32.reset(target, mode, bound_s)`, `mode` `run` or `bootloader`, runs `esptool --before
default-reset --after hard-reset|no-reset chip-id`: esptool's own reset sequences know the bridge chips and the
built-in USB-Serial/JTAG, so this code never drives DTR/RTS itself. `run` ends with the application running;
`bootloader` leaves the chip waiting in its ROM download mode. Both prove the chip answered, take the host lock,
and refuse on a held port, a protected unit or a target with no single match. **[derived]**

**The console.** Declaring a target also makes a console link named like it: the target's own USB bridge, found
by serial or by slot, **receive-only** (`host_tx_to_dut` false), at the target's baud (default 115200). The link
carries `own_bridge: true` and `dtr_rts_reach_dut: true`, and that pair is the ONE case the console accepts DTR or
RTS reaching the device, because on an ESP32 dev board they reach EN and IO0 by design. The port code still never sets
either line and still clears HUPCL. A target named like a wiring connection is listed with that problem and not
opened. A wiring connection cannot take a declared target's adapter (`adapter_taken`).

**The adapter is shared with esptool.** The console's port on a target's adapter is released (`Broker.suspend`) for
the length of every `flash`, `identify` and `reset`, and retaken (`Broker.resume`) after it, whatever the outcome;
the link keeps its byte offset, so the log carries on. In between the link reads `held`. Output printed before the
console reopens is lost; esptool returns right after the reset and the reopen follows in milliseconds, but a board
that boots faster than that has no guarantee. **[unknown]**: the exact effect of the kernel's DTR/RTS raise when the
broker opens the port, per bridge chip. The kernel raises both in one request on cp210x, ch341 and cdc-acm, which
leaves the usual auto-reset circuit idle, but no board has been measured.

## Crash decoding (slice 3)

**Built.** `esp32.decode(firmware, text)` finds the addresses in `text`: the PC of each `PC:SP` pair on a
`Backtrace:` line, and the `PC`, `MEPC` and `RA` of a panic header, in the order printed and once each, at most 128
(`truncated` says so). It resolves each against the ELF in the stored bundle, from two sources:

1. **the ELF's own symbol table**, read in pure Python (ELF32 little-endian, which is every ESP32 family): the function
   and the offset into it. It needs no toolchain, which the bench Pi does not have. Checked against `nm` on a real
   ESP32 ELF from the upstream debug-test build: 1064 functions parsed in 2 ms, addresses equal to `nm`'s.
2. **`addr2line` of the chip's toolchain, when it is on the PATH** (`xtensa-esp32{,s2,s3}-elf-addr2line`, or
   `riscv32-esp-elf-addr2line` for the C and H parts): the file and line. A run that fails or hangs is ignored whole,
   and the symbol-table answers stand. **[unknown]**: nobody has timed it; the 10 s bound only catches a hang.

A firmware with no ELF (an app-only image, an nRF package, a bundle whose `app_elf` is absent) is refused
`bad_package`, as is a stripped ELF. `text` is at most 128 KiB (1024 bytes a console line times 128 frames) and the ELF
at most 64 MiB. Addresses are used as printed: no return-address adjustment is made. The console page offers it on a
selected block of lines (step 4).

## Files this spec allows

`nrf_workbench/esp32/{__init__,package,targets,flash,commands}.py`; `nrf_workbench/programmer/firmware_store.py`
and `nrf_workbench/guards/checks.py` (the two package kinds); `nrf_workbench/service.py` (registration, the
capability `esp32`); `docs/specs/{esp32,firmware,api,core}.md`; `tests/esp32/`; `tests/programmer/`; the UI
files named in the UI step; `scripts/verify_dashboard.py`.
