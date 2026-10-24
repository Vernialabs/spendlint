"""`python -m spendlint ...` and the `spendlint` console script. See spendlint/cli.py for the commands."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
