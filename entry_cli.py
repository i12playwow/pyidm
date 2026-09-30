"""PyInstaller entry point for the PyIDM command-line interface."""
import sys

from idm.cli import main

if __name__ == "__main__":
    sys.exit(main())
