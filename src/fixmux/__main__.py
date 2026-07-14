"""Allow ``python -m fixmux`` to behave exactly like the ``fixmux`` script."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
