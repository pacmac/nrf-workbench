"""Load and validate a board profile (profiles/*.yaml). Facts about a board live in data, not code."""
import yaml


class ProfileError(Exception):
    pass


_REQUIRED_MODES = ("app", "bootloader")


def validate(p):
    if not isinstance(p, dict):
        raise ProfileError("a profile must be a mapping")
    for key in ("name", "family", "modes", "bootloader"):
        if key not in p:
            raise ProfileError("profile is missing '%s'" % key)
    for mode in _REQUIRED_MODES:
        ids = (p["modes"].get(mode) or {}).get("ids")
        if not ids or not all(_is_id(i) for i in ids):
            raise ProfileError("profile mode '%s' needs a non-empty list of ids like 239a:8029" % mode)
    both = set(p["modes"]["app"]["ids"]) & set(p["modes"]["bootloader"]["ids"])
    if both:
        raise ProfileError("an id cannot be in both the app and the bootloader set: %s" % ", ".join(sorted(both)))
    if "entry" not in p["bootloader"]:
        raise ProfileError("profile bootloader needs an 'entry' method")
    return p


def _is_id(text):
    parts = str(text).split(":")
    return len(parts) == 2 and all(len(x) == 4 and all(c in "0123456789abcdef" for c in x) for x in parts)


def load(path):
    try:
        with open(path, "r") as fh:
            data = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError) as exc:
        raise ProfileError("cannot read profile %s: %s" % (path, exc))
    return validate(data)
