"""Platform-neutral entrypoint. Windows loads the native Cython kernel."""
import os
from pathlib import Path
import sys

if os.name == 'nt':
    compiled = Path(__file__).resolve().parent / '.runtime' / 'compiled'
    sys.path.insert(0, str(compiled))
    # Import from compiled/.pyd rather than the same-named Python fallback.
    import descartes_search  # noqa: E402
    if not descartes_search.__file__.lower().endswith('.pyd'):
        raise SystemExit('Windows native kernel did not load')

from scheduler import main

if __name__ == '__main__':
    raise SystemExit(main())
