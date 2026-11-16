"""The nrf-workbench installer. Standard library only: it must run before anything is installed.

See docs/specs/install.md. The bootstrap (install/install.sh) hands over to install/install.py,
which calls cli.main().
"""

# Exit codes, shared with install.sh. A distinct code for "needs a person" lets a script tell
# "the plan is ready, nothing was applied" apart from a failure.
OK = 0
FAILED = 1
USAGE = 2
NEEDS_PERSON = 3
PREREQ = 4

MIN_PYTHON = (3, 10)   # the floor: the mcp SDK's own requirement (docs/specs/api.md)
MARKER = ".installed"  # the install record, in the prefix
