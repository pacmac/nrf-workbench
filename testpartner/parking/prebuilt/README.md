# The prebuilt parking package

`parking.zip` is a Nordic DFU package of the **parking** image: the bench's known-good, debuggable firmware for a
RAK4631 (the board sits on it between tests; it keeps USB enumerated so the workbench can see and touch it, and it
prints a heartbeat on the debug console). The installer puts it in the package store, in the project `testpartner`
(`docs/specs/install.md`, "the core system's own folder"), so a fresh install has something to flash.

| | |
|---|---|
| SHA-256 (the store's id) | `656d9084ea617dfaecfa4de652831d59db2c3df9e38cd6b33755125a6dffe1c5` |
| size | 56,571 bytes; image 55,768 bytes; 109 packets |
| SoftDevice required | 182; device type 82 |
| USB product string it announces | `nrfwb-parking 2` (found in the image, and read back from the board after a flash) |
| source | `testpartner/parking` at commit `df0255c` (2026-11-16 13:13 +0800), the last commit that touched it; the source has not changed since |
| built | 2026-11-16, on the dev host, before it was uploaded to the bench store at 15:33 +0800 |
| proved | flashed to the bench RAK4631 through the workbench, which came back as `nrfwb-parking 2` (2026-11-16) |

**Why it is committed and not built at install.** The build needs the RAK4631 board and variant definitions
(`NRFWB_RAK_DIR`, see `../platformio.ini`), which live in the mt-alarm project and are not in this repository, so
this repository alone cannot rebuild it. **A rebuild has not been compared with this file**, so reproducibility of
the build is unknown. If the source changes, build `env:parking`, replace `parking.zip`, and update the row above
and the hash in `tests/install/test_installer.py` together: a test fails if the file and this table disagree.
