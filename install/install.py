#!/usr/bin/env python3
"""The nrf-workbench installer entry point. Standard library only. See docs/specs/install.md.

Normally started by install/install.sh, which has already found a Python of at least 3.10 and
fetched the project. It can also be run directly:  python3 install/install.py --source . --check
"""
import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nrfwb_install.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
