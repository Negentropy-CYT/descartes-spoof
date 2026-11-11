# Spoof Search

Searches odd square cores C and composite m with gcd(C,m)=1, m=1 (mod 4), and sigma(C)(m+1)=2Cm, below a specified bound D=Cm.

## Portable application

Extract the complete package and open Spoof.exe. Keep its dependency folders alongside it.

Enter a bound such as 1e60 and the worker count. Use Full search for one computer, or Offline shards to divide the search across computers. GUI shard IDs start at 0: for eight shards, devices may own 0, 1-3, 4-5 and 6-7. Worker counts may differ. Search bound, total shards, split depth and subtrees per batch must match. Use the same program on every device.

Reported nodes include returned fragments of unfinished batches. On resume, the display starts at the saved checkpoint count.

Closing the window leaves the search running. Stop search terminates the engine and its workers. Resume search loads the latest valid checkpoint and recomputes uncommitted original batches. The checkpoint interval does not bound the amount of work that may be repeated. Resume with the same task settings, ownership and output directory; the worker count may change.

## Merge results

Click Merge results in the left panel, then Add results to select the original result JSON files from every device. Files may be added from different folders. Logs and checkpoints are not required. The window checks settings, ownership, batch counts, completion, unresolved work and HIT counts. It enables Merge and save only after all shards are covered without overlap. Saving re-reads the inputs and never permits overwriting an input result. The default output name is merged.json.

## Source and build

The source folder contains the Python/Cython source, assets, build scripts, primesieve source archive and compiled Windows x64 CPython 3.11 kernel. It excludes the development virtual environment and compiler toolchain. The portable application already includes its runtime dependencies.

To run the source, install Windows x64 CPython 3.11 with Tk support, then from the source directory:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install python-flint
.\start_gui.cmd
```

The bundled .pyd can be used directly. To rebuild the kernel, use build.ps1 -Toolchain with a toolchain containing build_env/Scripts/python.exe (Cython and setuptools), w64devkit/bin, build/primesieve/source/include and build/primesieve/build/libprimesieve.a.

To rebuild the portable application, install PyInstaller in .venv and run build_desktop.ps1 with empty OutputDir and WorkDir paths as needed. Keep build outputs outside the source directory. Do not replace code or the compiled kernel while a search is running.

The command-line merge entry is merge_shards.py; merge_results.ps1 collects s*.json files from InputDir. Command-line ownership remains zero-based. A local COMPLETE result covers only its assigned shards; global coverage requires successful merging of all shards.

## License

MIT License, Copyright (c) 2026 Chengyuan Tang. See LICENSE. Third-party components and distribution obligations are listed in THIRD_PARTY_NOTICES.md.
