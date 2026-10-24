"""`python -m costlint ...` and the `costlint` console script. See costlint/cli.py for the commands."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
