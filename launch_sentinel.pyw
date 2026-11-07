"""Console-less launcher used by run_sentinel.bat and by the optional
"Start with Windows" entry. Adds ./src to the import path and starts Sentinel."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from sentinel.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
