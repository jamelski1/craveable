"""PyInstaller entry point (equivalent to ``python -m craveable``)."""
import sys

from craveable.cli import main

if __name__ == "__main__":
    sys.exit(main())
