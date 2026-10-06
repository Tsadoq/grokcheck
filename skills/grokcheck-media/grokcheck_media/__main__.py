"""Entry point for `python3 <skill-dir>/grokcheck_media <command>`, no install needed.

Running the package directory puts that directory first on `sys.path`, where
its modules could shadow others; the skill directory takes its place so
`grokcheck_media` imports as a package.
"""

import sys
from pathlib import Path

sys.path[0] = str(Path(__file__).resolve().parent.parent)

from grokcheck_media.cli import main  # noqa: E402

sys.exit(main())
