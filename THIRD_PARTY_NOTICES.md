# Third-party notices

This project is distributed under the MIT License (see `LICENSE`). It uses the
following third-party components. Their licenses apply to those components and
are not replaced by the project's MIT license.

## primesieve (statically linked into the Windows kernel)

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

## python-flint

- License: MIT
- Source: https://github.com/flintlib/python-flint
- Used at runtime for large integer primality testing and factorization.

## FLINT

- License: GNU Lesser General Public License, version 2.1 or later
- Source: https://github.com/flintlib/flint
- The portable Windows package bundles `libflint` as a separate DLL.

## GMP

- License: GNU Lesser General Public License, version 3.0 or later
  (or GPL version 2.0 or later)
- Source: https://gmplib.org/
- The portable Windows package bundles `libgmp` as a separate DLL.

## MPFR

- License: GNU Lesser General Public License, version 3.0 or later
- Source: https://www.mpfr.org/
- The portable Windows package bundles `libmpfr` as a separate DLL.

## GCC runtime libraries

- License: GNU General Public License version 3 or later, with the
  GCC Runtime Library Exception
- The Windows extension links `libgcc` and `libstdc++` statically under that
  exception.

## Distribution checklist

When publishing a binary (`.pyd`, portable ZIP or installer), include:

- this file;
- the MIT `LICENSE` of this project;
- the BSD-2-Clause text above;
- the LGPL texts and notices for FLINT, GMP and MPFR if their DLLs are bundled;
- a written offer or link to the corresponding LGPL library sources.
