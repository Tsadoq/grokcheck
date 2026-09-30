"""Entry point for `python3 <skill-dir>/grokcheck <command>`, which needs no install.

Running the package directory puts that directory first on `sys.path`, where
its modules could shadow others; the skill directory takes its place so
`grokcheck` imports as a package.
"""

import sys
from pathlib import Path

sys.path[0] = str(Path(__file__).resolve().parent.parent)

from grokcheck.cli import main  # noqa: E402

sys.exit(main())
