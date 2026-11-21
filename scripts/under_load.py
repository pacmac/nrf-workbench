#!/usr/bin/env python3
"""Run a command while the CPUs are busy, so a test that only fails when the host is loaded fails here.

    python3 scripts/under_load.py [--hogs N] -- <command> [args...]

Starts N busy loops (default three per CPU, which is what a full test run of this suite does to a two-core host once the threads of a whole suite are counted), runs the command, stops the loops and exits with the
command's own exit code. Nothing is written anywhere. Used by the register to try and to prove an intermittent fault (`idiot try --runs`).
"""
import argparse
import multiprocessing
import os
import subprocess
import sys


def hog():
    while True:
        pass


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--hogs", type=int, default=None, help="busy loops to run (default: three per CPU)")
    ap.add_argument("command", nargs=argparse.REMAINDER)
    args = ap.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        ap.error("give the command to run after --")
    n = args.hogs if args.hogs is not None else 3 * (os.cpu_count() or 1)
    workers = [multiprocessing.Process(target=hog, daemon=True) for _ in range(n)]
    for w in workers:
        w.start()
    try:
        return subprocess.call(command)
    finally:
        for w in workers:
            w.terminate()
        for w in workers:
            w.join()


if __name__ == "__main__":
    sys.exit(main())
