# Third-party notices

This project is distributed under the MIT License (see `LICENSE`). It uses the
following third-party components. Their licenses apply to those components and
are not replaced by the project's MIT license.

## Component versions in the Windows x64 build

The Windows x64 build bundles runtime DLLs shipped with
python-flint 0.9.0:

- python-flint 0.9.0
- FLINT 3.6.0
- GMP 6.3.0
- MPFR 4.2.2
- winpthreads and GCC runtime libraries from the same wheel

The license files for these components are collected from the installed
python-flint distribution at build time. The `licenses/` directory in the
Windows x64 build preserves the upstream license layout.

## primesieve 12.17 (statically linked into the Windows kernel)

- License: BSD 2-Clause
- Copyright (c) 2010 - 2026, Kim Walisch. All rights reserved.
- Source: https://github.com/kimwalisch/primesieve
- The source archive and the full license are included in `vendor/primesieve.zip`.

```
BSD 2-Clause License

Copyright (c) 2010 - 2026, Kim Walisch.
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

## python-flint 0.9.0

- License: MIT
- Source: https://github.com/flintlib/python-flint
- Used at runtime for large integer primality testing and factorization.

## FLINT 3.6.0

- License: GNU Lesser General Public License, version 3.0 or later
- Source: https://flintlib.org/download/ (source archive `flint-3.6.0`)
- The Windows x64 build bundles `libflint` as a separate DLL.
- FLINT's `COPYING.LESSER` is LGPLv3 and its `COPYING` is GPLv3. LGPLv3
  incorporates the terms of GPLv3, so both texts are included in `licenses/`.

## GMP 6.3.0

- License: GNU Lesser General Public License, version 3.0 or later
- Source: https://gmplib.org/download/gmp/gmp-6.3.0.tar.xz
- The Windows x64 build bundles `libgmp` as a separate DLL.

## MPFR 4.2.2

- License: GNU Lesser General Public License, version 3.0 or later
- Source: https://www.mpfr.org/mpfr-4.2.2/
- The Windows x64 build bundles `libmpfr` as a separate DLL.

## winpthreads

- License: MIT and BSD-3-Clause-Clear, as declared by python-flint 0.9.0
- Source: https://github.com/mingw-w64/mingw-w64
- The Windows x64 build bundles `libwinpthread` as a separate DLL.

## GCC runtime libraries

- License: GNU General Public License version 3 or later, with the
  GCC Runtime Library Exception
- The wheel also includes the LGPL-2.1 text for the bundled runtime files.
- The Windows extension links `libgcc` and `libstdc++` statically under that
  exception.

## Source availability

The Windows x64 build bundles DLLs from the python-flint 0.9.0 wheel. The
corresponding upstream source versions are:

- python-flint 0.9.0: https://github.com/flintlib/python-flint
- FLINT 3.6.0: https://flintlib.org/download/
- GMP 6.3.0: https://gmplib.org/download/gmp/gmp-6.3.0.tar.xz
- MPFR 4.2.2: https://www.mpfr.org/mpfr-4.2.2/
- winpthreads: https://github.com/mingw-w64/mingw-w64/tree/master/mingw-w64-libraries/winpthreads
- GCC runtime: https://gcc.gnu.org/releases.html

The FLINT, GMP and MPFR DLLs are bundled separately, so they can be replaced
without rebuilding the application. The license texts and upstream source
locations are provided so that recipients can exercise the rights granted by
the LGPL.

## Distribution checklist

When publishing a binary (`.pyd`, Windows x64 ZIP or installer), include:

- this file;
- the MIT `LICENSE` of this project;
- the `licenses/` directory collected from the installed python-flint
  distribution, preserving its upstream layout;
- the BSD-2-Clause text above for primesieve;
- the LGPLv3 and GPLv3 texts for FLINT;
- the LGPL texts for GMP and MPFR;
- the winpthreads and GCC runtime license texts;
- the source availability information above.
