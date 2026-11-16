"""The feature manifest: which features exist, what each needs, and the pins that were verified."""
import hashlib
import json
import os


class FeatureError(Exception):
    pass


def load(source):
    path = os.path.join(source, "install", "features.json")
    try:
        with open(path, "r") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        raise FeatureError("cannot read the feature manifest %s: %s" % (path, exc))


def resolve(manifest, requested):
    """The ordered list of features to install, with what each needs pulled in first.
    A feature that is 'later' is refused by name, not silently skipped."""
    known = manifest["features"]
    out = []

    def add(name):
        if name not in known:
            raise FeatureError("unknown feature '%s' (known: %s)" % (name, ", ".join(sorted(known))))
        if known[name].get("status") == "later":
            raise FeatureError("feature '%s' is not available yet in this release" % name)
        for dep in known[name].get("needs", []):
            add(dep)
        if name not in out:
            out.append(name)

    for name in requested:
        add(name)
    return out


def pins(manifest, features):
    out = []
    for name in features:
        for pin in manifest["features"][name].get("pip", []):
            if pin not in out:
                out.append(pin)
    return out


def pins_hash(pin_list):
    return hashlib.sha256("\n".join(sorted(pin_list)).encode("utf-8")).hexdigest()[:16]
