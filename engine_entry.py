"""PyInstaller entry point for the Windows native search engine."""
from pathlib import Path
import multiprocessing
import os
import sys

# Both the main process and spawned workers must prefer the native .pyd.
if os.name == 'nt':
    compiled = Path(__file__).resolve().parent / '.runtime' / 'compiled'
    sys.path.insert(0, str(compiled))

if __name__ == '__main__':
    multiprocessing.freeze_support()  # required by frozen Windows multiprocessing
    from runner import main
    raise SystemExit(main())
