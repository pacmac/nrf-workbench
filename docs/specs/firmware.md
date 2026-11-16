# Firmware — the store, upload, flash by id, and streaming (DRAFT, not agreed)

How a firmware image gets from where it was built to a board, through the host that has the USB
cable, with no `scp`. Peter, 2026-11-16: "scp-ing a file to the device is not really practical ... my
thoughts are that the workbench should support streaming as well as having a file structure to allow
files to be uploaded to it via the api and select a file to flash as well as streaming a file to the
device." Inputs: `programmer.md`, `api.md`, `core.md`, and what was built and proved on the bench.
Tags: **[stated]** Peter said it; **[verified]** read in code or run; **[derived]** worked out from a
constraint, Peter may overrule; **[unknown]** nobody has looked.

Area `programmer` (the store and the flash commands), with the HTTP, CLI and MCP shapes generated
from the registry as `api.md` already says. No new area.

## Files this spec allows

This spec itself, `docs/specs/firmware.md`, and, in the same task, the cross-reference edits it
names: `docs/specs/core.md` (the new result codes), `docs/specs/api.md` and
`docs/specs/programmer.md` (a pointer to this spec each). **No code is written under this spec; code
follows once Peter has agreed it**, in `nrf_workbench/programmer/firmware_store.py` and the registry
rows, with `tests/programmer/test_firmware_store.py`.

## Status

**Built and tested, 2026-11-16 (Peter: "go"):** the store library, `nrf_workbench/programmer/firmware_store.py`
(`put`, `list`, `show`, `delete`, `resolve`, `verify`), with 42 tests that run on any host with no
hardware. Twenty mutants of its guards were each caught by a test (the read bound, the free-space
check and its boundary, the short-body check, the checksum compare and its order before the package
check, the package check, temp-file cleanup, idempotence, the re-hash, the in-use check, the id
prefix floor, the stale-temp lock and the lock held past the write handle, the record-id check, the
ordering, the length checks, the full-disk branch and the path scrub).

**Files for the flash-by-id unit (added 2026-11-16, Peter: "go"):**
`nrf_workbench/programmer/flash_firmware.py`, `tests/programmer/test_flash_firmware.py`, and a small
`held=` parameter on `nrf_workbench/devices/entry.py` (`enter_bootloader`) and
`nrf_workbench/programmer/flash_file.py` with a test each in `tests/devices/test_entry.py` and
`tests/programmer/test_flash.py`. **Why `held=`:** `programmer.flash` is two steps (enter, then flash),
and each took and released the host lock itself, which left a gap in which another instance could act.
The caller now takes the lock once and passes it down. **Why the profile check comes first:** the
SoftDevice comparison was made inside the flash step, after the touch; a package for the wrong
SoftDevice would then be found only after the board was already in its bootloader. `programmer.flash`
runs it before any touch (`wrong_softdevice` with the board untouched).

**Built and tested, 2026-11-16: `programmer.flash` by id** (`flash_firmware.py`, 20 tests on the fake
tree with a fake touch and uploader). It re-hashes the stored image, checks the package against the
profile before any touch (**tested: a wrong-SoftDevice package is refused with zero touches and the
board still in its app**), takes ONE lock held across enter and flash (**tested: an intruder cannot
take it at the touch or at the upload**), enters once only if the board is in its app, uses the image
itself as the declared way back (a caller's bogus declaration is replaced), flashes once, never
retries, checks the product string when asked, and reports `entered_bootloader` as true, false, or
unknown (a failed touch is unknown, never "not entered"). Seventeen mutants were killed; two survived
at first and exposed a real gap (a flash bound of 0 touched the board before the flash step rejected
it) and a test that only passed because garbage collection released the lock by accident; both are
fixed. One mutant is **equivalent and left alone**: removing the outer empty-serial check changes
nothing, because the entry step refuses an empty serial first with the same code and before any touch.

**Files for the registration unit (added 2026-11-16, Peter: "go"):**
`nrf_workbench/programmer/commands.py` (the six rows below, registered through `register(registry,
host)` as `architecture.md` requires), `nrf_workbench/devices/commands.py` (`devices.list`, because a caller
cannot name a serial to flash without being able to read which boards are attached), and
`tests/programmer/test_commands.py`. The rows go through the registry, so the same handlers will serve
HTTP, the CLI and the MCP. **`host` is a small contract, not a class:** `config`, `sysfs`, `devroot`, `proc`,
`lock_path`, `profiles`, `units`, `hooks` (test seams: touch, runner, tool, watcher, ledger; empty in
production) and `service(name, factory)` (a cached lazily-built singleton). The concrete host belongs
to the composition root (`service.py`), not to an area.

**Built and tested, 2026-11-16: the six rows and `devices.list`, registered through the registry**
(`programmer/commands.py`, `devices/commands.py`; 37 tests through `Registry.call` with a fake host;
19 mutants caught). `programmer.flash_stream` is built here too: **a test proves the whole file is in the
store, complete, before the board is touched**, that a refused flash (an absent board, a held lock) **keeps
the upload** and reports its id so the retry is by id, and that an invalid upload, a wrong checksum or
a missing length is refused before any touch. Three survivors at first were real test gaps (one
profile, one mode at a time, one board) and are fixed: the profile that claims the board is used, not
the first one loaded.

**Proved on the bench, 2026-11-16 (the acceptance criterion in `VERIFY` below):** the service ran on
the Pi (loopback only, reached from the build host through an SSH tunnel), and from the build host over
HTTP, the build host never opening the board:
1. `devices.list` named the attached board; `firmware.put` stored the pinscan package (58,299 bytes, id
   `6475e0be…`, SoftDevice 182, 113 packets), the Pi's store directory held exactly `<id>.zip` and
   `<id>.json`, both mode 0600;
2. `programmer.flash` **by id**: ok, entered the bootloader once (handover 0.482 s, serial the same
   across the mode change), 113 of 113 marks in 4.653 s (12,357 B/s), the board returned reporting
   `nrfwb-pinscan 1`;
3. `programmer.flash_stream` **in one call** (the file as the request body, 56,571 bytes): ok, stored
   as `656d9084…` before the touch, 109 of 109 marks in 4.412 s (12,640 B/s), the board returned
   reporting `nrfwb-parking 2`;
4. checked independently of the service: the Pi's own USB view showed `nrfwb-parking 2` on the same serial,
   and the debug dongle read 5 heartbeats in 5 s from the firmware itself.
The two throughputs sit at the low end of the 12.35 to 15.42 kB/s range recorded earlier (small images).
**Not exercised:** the non-loopback and token path on the Pi (it is tested on this host only), TLS, and a
dropped upload against the real store on the Pi (tested on this host against a real socket).

**Not built yet:** the CLI and the MCP front ends, the dashboard, a systemd unit.

**Not tested, on purpose or by limit:** the order in which `delete` removes the two files (a crash-timing property: record first, so a crash leaves an
unlisted image and never a listed entry with no image), and the `fsync` calls (a disk-level effect a
unit test cannot see). The package check reuses `guards.checks.dfu_package` with an empty profile, so
the SoftDevice comparison is skipped at upload and made at flash, exactly as specified.

## GAP defects first

1. **Today a flash needs the file already on the host's disk, put there by hand.** The proven path
   (`programmer.flash_file`) takes a path; the Pi's copy of the mt-alarm and parking images got there
   by `scp` **[verified]**. That is a development convenience, not a product: an AI on another machine
   cannot do it, and it leaves no record of what was flashed.
2. **A path on the service host is the wrong thing for a caller to name.** It lets a caller point the
   flasher at any file the service can read, and it ties the caller to the host's directory layout.
   The caller should name **content** (an id), not a place.
3. **The board is erased the moment the transfer starts** (the bootloader's START packet erases the
   application **[verified: programmer.md, from the Adafruit bootloader source]**). A flash fed
   directly from a network socket would leave a board erased if the connection dropped halfway. So
   "streaming" can only ever be a stream **into the host**, never straight into the device.
4. **`programmer.md` says a build is separate from a flash and has no place for the built file to
   live between the two.** This spec is that place.
5. **The MCP cannot carry a binary the way HTTP can.** A 332,092-byte image is about 440 KB as base64
   inside a tool call, which an AI would have to read back and emit. Not workable. The path for MCP is
   different (see MCP).

## TOOL the picture

```
build host / AI                    workbench host (the Pi)                      board
firmware.zip ──put (HTTP body)──►  store: <sha256>.zip + record
              ◄── id, sha256 ────   validated as a DFU package on arrival
flash <id> <serial> ────────────►  lock, guards, (touch), adafruit-nrfutil reads
                                    the STORED file from local disk, USB ──────►  writes flash
              ◄── Result JSON ───   verify identity and product string
```

Two hops that never merge: **network** (caller to host) and **USB** (host to board). The caller never
talks to the board, and the flasher never reads from a socket.

## TOOL the store

- **A directory on the host, `service.firmware_dir`, a configuration value.** It is chosen at install
  (the installer asks, with a default under the install's own data directory) and **nothing assumes a
  path** such as `/usr/share/pac` **[stated: that is Peter's preferred folder on his hosts, never a
  product default]**.
- **An entry is two files: `<sha256>.zip` (the image, exactly the bytes received) and
  `<sha256>.json` (the record).** The record holds: `sha256`, `bytes`, `name` (a label, free text),
  `uploaded_at`, who uploaded it (the token's label, not the token), and what the package check found
  (`image_bytes`, `softdevice_req`, `packets`). Only `put` creates an entry; a file copied into the
  directory by hand has no record and is **not listed and never flashed**.
- **The id is the SHA-256 of the file, in hex.** Uploading the same bytes twice gives the same id, so
  an upload is idempotent and two names for one image are one image. A caller may use any unique
  prefix of at least 8 hex characters; an ambiguous or unknown one is refused (`firmware_unknown`).
  **[derived: content addressing means an id cannot point at different bytes later, which a name or a
  counter could.]**
- **No client-supplied string is ever used in a filesystem path.** The `name` is data in the record
  and nothing more; the stored file name is the hash. Path traversal is impossible by construction,
  not by filtering. **[derived]**
- **The store is never cleaned automatically.** An old image may be the only way back to a known
  state (the bench RAK's original firmware exists only as a package, because flash cannot be read back
  over USB **[verified: programmer.md]**). When the filesystem cannot hold an upload, `put` refuses
  with `store_full`; a person deletes. **[derived: a silent eviction destroys the thing someone
  needs at the worst moment.]** There is no item count or byte cap invented here; if Peter wants a
  budget it is a configured number with no default.
- **A package that is a unit's declared return path cannot be deleted** (`in_use`, naming the unit),
  because `devices.md` refuses entry to the bootloader without one.

## TOOL the commands

All are registry rows, so the CLI, the HTTP API and the MCP tools all appear from them (`api.md`).
Every one that changes anything is a `write`, so a token is needed even in `open-read` mode.

| command | effect | arguments | result data |
|---|---|---|---|
| `firmware.put` | write | the file bytes, `name` (optional), `sha256` (optional) | `id`, `sha256`, `bytes`, `name`, `package` |
| `firmware.list` | read | none | each record, newest first |
| `firmware.show` | read | `id` | the record, and which units name it as a return path |
| `firmware.delete` | write | `id` | the id removed |
| `programmer.flash` | write | `serial`, `firmware` (an id), the two bounds, `expect_product` (optional) | the flash Result, with `firmware.id` in it |
| `programmer.flash_stream` | write | the file bytes, `serial`, the two bounds, `expect_product`, `name` | the flash Result, plus the new id |

### `firmware.put` — upload

- **The body of the request is the file**, not JSON and not multipart: `POST /api/firmware`, the
  raw bytes, `name` and `sha256` as query parameters. This is the one deliberate departure from
  "a `write` takes a JSON body" in `api.md`, and the row declares it (`body: raw`), so it is still
  generated and still in the command list. **[derived: a binary in JSON must be encoded, which costs a
  third more and needs the whole thing in memory.]**
- **A `Content-Length` is required; a chunked body with none is refused (411).** The length is the
  honest size and the free-space check (`store_full`) is made against it **before a byte is written**,
  using the filesystem's free space as reported by the operating system.
- **The body is streamed to a temporary file inside the store directory** (the same filesystem, so
  the final move is atomic), hashing as it arrives. **It is read to exactly `Content-Length` bytes and
  no more.**
- **Nothing becomes visible until the whole file has arrived and passed.** Then, in this order: the
  hash is compared with the client's `sha256` if one was given (`checksum_mismatch`), the file is
  checked as a DFU package (the same `checks.dfu_package` the flash uses **[verified: it exists and
  returns the image size and SoftDevice requirement]**: a zip, a `manifest.json`, the image and its
  init data) and refused as `bad_package` if it is not one, then the file is moved to
  `<sha256>.zip` and the record written. **A dropped connection, a short body, a bad package or a
  crash leaves no entry and no temporary file behind** (a stale temporary is removed at start).
- **Compatibility with a particular board is not checked at upload**, because the store does not
  know the target. The SoftDevice requirement is recorded, and checked against the board's profile
  when a flash is requested (`wrong_softdevice`), exactly as today. **[derived]**
- **Uploads do not take the host lock** (nothing touches hardware) and may run concurrently. Two
  uploads of the same bytes converge on one entry.

### `programmer.flash` — flash by id

Resolves the id, **re-hashes the stored file and compares it with the id** (a store that has been
altered or damaged refuses with `bad_package` and names the mismatch, before any hardware is touched),
then runs the flow that is already built and proven:

1. the host lock, then the guards (the serial, not protected, the port not held);
2. **if the board is running its application,** the bootloader entry, with **this same package as the
   declared return path** (so the entry's own "a way back must exist" guard is satisfied by the thing
   being flashed); **if it is already in its bootloader,** straight to the flash. The entry is the
   one touch; it is never repeated **[verified: `entry.py`, the touch ledger]**;
3. `flash_file` against the stored file's local path, one attempt;
4. the board must return as the application on kernel events, with the same serial and, when
   `expect_product` is given, the product string the image announces **[verified: built and proved,
   2026-11-16, `nrfwb-parking 2`]**.

**Two bounds, both required, neither defaulted** (`core.md`): `enter_bound_s` and `flash_bound_s`.
They are derived from the recorded measurements the service publishes (`api.md`, Measurements): the
entry handover took 0.362 s and 0.370 s in the two cycles whose figure was kept, and seven flashes of
53 to 332 KB images ran at 12.35 to 15.42 kB/s on the RAK4631 **[verified on the bench, 2026-11-16]**,
so a caller computes them from the image size. (Seven is not a distribution; the service records every
measurement it makes, `api.md`, and the range widens or holds as it does.)
**The upload time is not part of either bound** and is not the service's to decide.

### `programmer.flash_stream` — upload and flash in one call

The same as `firmware.put` followed by `programmer.flash`, in one request, with the file as the body
and everything else as query parameters.

- **The file is received and validated in full first, then stored, then flashed from the store.**
  Gap 3 is the reason: the board must not be erased until the whole file is held and checked.
- **If the host lock is held, the request is refused with `lock_held` after the upload.** The file is
  already in the store, so the retry is `programmer.flash` by id and nothing is sent twice.
  **[derived: the lock is for hardware and the upload does not need it; refusing before the upload
  would waste nothing but refusing after keeps the file.]**
- The result carries the id, so the next flash of the same image needs no upload.

## TOOL how each front end reaches it

- **HTTP** (the routes the registry generates, one shape for every command, **corrected 2026-11-16**:
  an earlier draft here named RESTful routes such as `DELETE /api/firmware/<id>`, which `api.md` does not
  generate): `POST /api/firmware/put` and `POST /api/programmer/flash_stream` with the raw bytes as the
  body and the other arguments as query parameters; `GET /api/firmware/list`;
  `GET /api/firmware/show?id=`; `POST /api/firmware/delete` and `POST /api/programmer/flash` with a JSON
  object body; `GET /api/devices/list`. **Built and tested over a real ASGI client, 2026-11-16.**
- **CLI, two modes, one command set:**
  - **on the host that has the board** the CLI runs the library in-process with no service
    (`api.md`, CLI and service) and reads a local file directly: `flash FILE` is `put` then `flash`
    on the local store, so the proven path keeps working with the service down;
  - **from another machine** it is given `--host` and `--token` and every command goes over HTTP;
    `put FILE` streams the file as the body and prints the id. It computes the SHA-256 itself and
    sends it, so a corrupted transfer is caught as `checksum_mismatch`, not found later. **[derived]**
- **MCP:** the MCP server is a thin client of the HTTP service (`api.md`), so it runs on the AI's
  machine, **where the build output is**. `firmware_put` therefore takes **a path on the machine the
  MCP server runs on**, and the server streams that file to the service over HTTP; the AI never reads
  or encodes the bytes. `firmware_put` also takes a `url` the **service** fetches (for a build
  artefact held elsewhere), with the same validation. A path is read as a regular file only, is
  hashed as it is read, and is checked as a DFU package by the service like any other upload.
  **[derived: this is the only way gap 5 is not a problem; the only requirement it adds is that the
  MCP server and the file share a machine, which holds for a stdio server. A remote streamable-HTTP
  MCP server reads paths on its own host. [unknown: the SDK's transports were not exercised.]]**

## TOOL who may do it, and what travels

- All writes need the token (`api.md`); an upload is a write. The token and the file cross the
  network **unencrypted** on plain HTTP (`api.md`, listening). A firmware image is not secret, but the
  token that lets a caller flash hardware is; this is stated, not hidden, and TLS is still **not
  designed** **[unknown]**.
- An image is a program that will run on a board. A valid DFU package from a valid token is trusted to
  that extent; the guards protect the board's identity and state, not the program's content. There is
  no signing in this spec. **[unknown: whether a bench wants it.]**

## DECIDE derived (Claude; Peter may overrule)

1. **Content-addressed store (id = SHA-256).** Constraint: an id must not be able to mean different
   bytes later; a name or counter can. Runner-up: caller-chosen names, rejected because two uploads
   under one name would silently replace the thing a recorded flash refers to.
2. **A raw body for upload, not JSON or multipart.** Constraint: a binary of a few hundred KB; JSON
   needs an encoding that costs a third more and holds it all in memory; multipart needs parsing for
   nothing. Runner-up: multipart, which browsers need for a form; the dashboard can send a raw body from
   a script, so it is not needed.
3. **Receive, validate, store, then flash; never feed the device from the socket.** Constraint: gap 3.
4. **Refuse `store_full`, never evict.** Constraint: the only copy of a way back may be in the store.
5. **A required `Content-Length`.** Constraint: a free-space check and a read bound both need the true
   size before the first byte; a chunked body has neither.
6. **Two required bounds.** Constraint: the derive rule: one bound for two different things (a
   0.4 s handover and a 20 s write) is a bound for neither.
7. **The MCP uploads from the machine it runs on, and the service can also fetch a URL.** Constraint:
   gap 5.

## VERIFY

On a host with fake `/sys` and `/dev` and a temporary store, then on the bench Pi:

- **Put:** a valid package is stored once; the same bytes again return the same id and add nothing; the
  record's `softdevice_req` and `image_bytes` match the package.
- **Put refuses and leaves nothing:** a non-zip, a zip with no manifest, a body shorter than its
  `Content-Length`, a connection dropped mid-body, a wrong client `sha256` (`checksum_mismatch`), a
  body larger than free space (`store_full`, nothing written), a chunked body (411). Each leaves the
  store directory as it was, with no temporary file.
- **Names are data:** a `name` of `../../etc/x` is stored as a label and no file is created outside the
  store.
- **Flash by id:** the stored file is the one flashed (the command's `-pkg` argument is the store's
  own path); an unknown or ambiguous prefix is `firmware_unknown` with nothing touched; **a stored file
  whose bytes were altered after upload is refused before any hardware is touched**; a package that
  needs another SoftDevice is `wrong_softdevice` at the flash, not at the upload.
- **Delete:** refused with `in_use` for a unit's declared return package; allowed otherwise.
- **Stream:** the file is complete and valid before the touch happens (an ordering test); a held lock
  refuses after the upload and the file is in the store.
- **Mutants:** removing the re-hash, the length bound, the temporary-file cleanup, the atomic move, or
  the in-use check each turns a test red.
- **On the bench:** from the build host, `put` the parking image to the Pi over HTTP and
  `flash` it by id, ending with the board reporting `nrfwb-parking 2` and the same serial, and the
  activity record naming the id. The build host never opens the board.
