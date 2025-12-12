"""Allow running the CLI as a module: python -m physionet."""

import sys
from physionet.cli import main

if __name__ == "__main__":
    sys.exit(main())
