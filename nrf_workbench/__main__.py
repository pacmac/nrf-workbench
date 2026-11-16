"""python -m nrf_workbench ...  : the command line (local mode builds the host here, the composition root)."""
import sys

from . import service
from .cli.app import run

if __name__ == "__main__":
    sys.exit(run(sys.argv[1:], local=service.local))
