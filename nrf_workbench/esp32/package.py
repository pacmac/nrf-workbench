"""ESP32 firmware packages (docs/specs/esp32.md, "Packages"): what the store accepts besides an nRF DFU zip.

Two kinds, told apart from the bytes and never from a file name:

  esp-image   one whole-flash image, written at 0x0. A partition table sits at flash offset 0x8000 on every ESP32
              family and starts with the magic bytes AA 50, so a file that has them at that offset is a whole image
              (a merged or factory image). An app-only .bin has no offset the store could know, so it is refused.
  esp-bundle  a zip holding ESP-IDF's `flasher_args.json` and the files its `flash_files` map names. The optional
              `app_elf` key names the ELF kept for crash decoding.

READ-ONLY: it opens the file and nothing else.
"""
import json
import os
import zipfile

KIND_IMAGE, KIND_BUNDLE = "esp-image", "esp-bundle"
PARTITION_TABLE_AT = 0x8000
PARTITION_MAGIC = b"\xaa\x50"
IMAGE_MAGIC = 0xE9                      # first byte of every ESP application and bootloader image
ARGS_NAME = "flasher_args.json"


class Bad(Exception):
    """The file is an ESP package that is wrong, or looks like one and cannot be flashed; the text says why."""


def kind_of(path):
    """esp-image, esp-bundle, or None when this is not an ESP package (the caller then tries other kinds)."""
    try:
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as z:
                return KIND_BUNDLE if ARGS_NAME in z.namelist() else None
        with open(path, "rb") as fh:
            head = fh.read(PARTITION_TABLE_AT + len(PARTITION_MAGIC))
    except (OSError, zipfile.BadZipFile):
        return None
    if head[PARTITION_TABLE_AT:PARTITION_TABLE_AT + 2] == PARTITION_MAGIC:
        return KIND_IMAGE
    return None


def single_part_message(path):
    """The reason an app-only image is refused, or None if the file does not look like an ESP image part."""
    try:
        with open(path, "rb") as fh:
            first = fh.read(1)
    except OSError:
        return None
    if first and first[0] == IMAGE_MAGIC:
        return ("this is a single ESP image (bootloader, partition table or application), not a whole-flash image: "
                "it carries no flash offset. Upload a merged image, or a zip with ESP-IDF's flasher_args.json")
    return None


def _offset(text):
    try:
        value = int(str(text), 0)
    except ValueError:
        raise Bad("flasher_args.json: %r is not a flash offset" % (text,))
    if value < 0:
        raise Bad("flasher_args.json: a flash offset cannot be negative")
    return value


def inspect(path):
    """{kind, parts: [{offset, name, bytes}], image_bytes, chip, elf} for a package `kind_of` accepted. Raises Bad."""
    kind = kind_of(path)
    if kind == KIND_IMAGE:
        size = os.path.getsize(path)
        return {"kind": kind, "parts": [{"offset": 0, "name": os.path.basename(path), "bytes": size}],
                "image_bytes": size, "chip": None, "elf": False}
    if kind != KIND_BUNDLE:
        raise Bad("not an ESP package")
    try:
        with zipfile.ZipFile(path) as z:
            args = json.loads(z.read(ARGS_NAME).decode("utf-8"))
            files = args.get("flash_files")
            if not isinstance(files, dict) or not files:
                raise Bad("flasher_args.json has no flash_files map")
            parts, seen = [], set()
            for off_text, name in files.items():
                offset = _offset(off_text)
                if offset in seen:
                    raise Bad("flasher_args.json names offset 0x%x twice" % offset)
                seen.add(offset)
                if not isinstance(name, str) or name not in z.namelist():
                    raise Bad("flasher_args.json names %r, which is not in the zip" % (name,))
                parts.append({"offset": offset, "name": name, "bytes": z.getinfo(name).file_size})
            parts.sort(key=lambda p: p["offset"])
            if not all(p["bytes"] for p in parts):
                raise Bad("flasher_args.json names an empty file")
            for a, b in zip(parts, parts[1:]):
                if a["offset"] + a["bytes"] > b["offset"]:
                    raise Bad("%s (0x%x, %d bytes) runs into %s at 0x%x" % (a["name"], a["offset"], a["bytes"],
                                                                         b["name"], b["offset"]))
            chip = (args.get("extra_esptool_args") or {}).get("chip")
            elf_name = args.get("app_elf")
            if elf_name is not None and elf_name not in z.namelist():
                raise Bad("flasher_args.json names an ELF %r that is not in the zip" % (elf_name,))
    except (zipfile.BadZipFile, KeyError, ValueError, OSError) as exc:
        raise Bad("the zip cannot be read as an ESP bundle: %s" % exc)
    return {"kind": kind, "parts": parts, "image_bytes": sum(p["bytes"] for p in parts),
            "chip": chip if isinstance(chip, str) else None, "elf": elf_name is not None,
            "elf_name": elf_name}
