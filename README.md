# descartes-spoof

`descartes-spoof` performs a bounded exact search for positive odd spoofs of the classical form. Given a numerical upper bound, it tests whether any solutions other than Descartes' example exist below that bound. The program enumerates the prime factorizations of the square part, solves the perfect number equation for the unique remaining factor, and uses mathematical pruning to eliminate impossible branches. It supports multiple worker processes, sharded execution, and checkpoint recovery. Results record hits, search status, and unresolved cases. The current implementation has completed an exhaustive search below `10^60` and found no solutions other than Descartes' example.

A perfect number is a positive integer `N` satisfying `σ(N)=2N`, where `σ` denotes the sum of the positive divisors; for example, `σ(6)=1+2+3+6=12=2×6`. All known perfect numbers are even, and the existence of odd perfect numbers remains an open problem.

Descartes (1638) gave a famous example in which the composite number `22021` is treated as prime. For a prime `p`, we have `σ(p)=p+1`; treating a composite number `m` in the same way amounts to replacing `σ(m)` with `m+1`. The resulting number formally satisfies the perfect number equation, but is not an odd perfect number:

```math
3^2\cdot7^2\cdot11^2\cdot13^2\cdot22021=198585576189,
\qquad
22021=19^2\cdot61.
```

Such objects are called spoofs (singular: spoof). Descartes' construction retains the prime factorization of the square part while treating the composite factor `m` as prime; the resulting number formally satisfies the perfect number equation but is not perfect. Any argument that remains valid when `m` is treated as prime also applies to a spoof. Spoofs can therefore be used to examine which arguments rely only on the formal equation.

Before the 2022 paper by the BYU Computational Number Theory Group, Descartes' example was the only known nontrivial positive odd spoof. Voight (2003) gave a generalized spoof with a negative base: `3^4·7^2·11^2·19^2·(-127)`. Banks et al. (2008) and Dittmer (2014) searched for positive odd spoofs of related forms, but found no additional examples beyond Descartes'. In particular, Dittmer proved that, under his definition, Descartes' example is the only spoof with fewer than seven distinct quasi-prime factors. BYU (2022) studied a broader definition allowing arbitrary integer bases, including negative bases. They gave even examples such as `3^1·4^1·5^1`, in which the composite base `4` is formally treated as prime. They also determined all 21 nontrivial, odd, primitive spoof perfect factorizations with fewer than seven bases.

These studies focus on particular forms of factorization or on factorizations with a bounded number of bases. Tóth (2021, Theorem 1) established that, under his definition, every odd spoof perfect number other than Descartes' example has a non-pseudo-prime component greater than `10^12`. This result relies on an exhaustive computation of the relevant set up to that bound. In the notation used below, this is a lower bound on `C`, rather than on the full product `D=Cm`.

This project exhaustively searches the classical form below a specified numerical bound on the full product `D=Cm`, without a separate parameter restricting the number of bases.

## Mathematical background

For a positive integer `n`, define

```math
\sigma(n)=\sum_{d\mid n}d,
\qquad
I(n)=\frac{\sigma(n)}{n}.
```

The quantity `I(n)` is called the abundancy index; a perfect number satisfies `I(N)=2`.

Euler proved that if an odd perfect number exists, exactly one prime in its prime factorization has an odd exponent and all other exponents are even. Hence it can be written as

```math
N=q^\alpha r^2,
\qquad
\gcd(q,r)=1,
\qquad
q\equiv\alpha\equiv1\pmod 4,
```

where `q` is the special prime and `r` is a positive odd integer.

Descartes' construction replaces `q` with a possibly composite number `m`, takes its exponent to be 1, denotes the square part by `C`, and requires

```math
\sigma(C)(m+1)=2Cm.
```

If `m` is prime, `m≡1 (mod 4)`, `C` is an odd square, and `gcd(C,m)=1`, this equation makes `Cm` an odd perfect number. If `m` is composite and the equation still holds, the result is a spoof. Since a composite `m` satisfies `σ(m)>m+1` and `gcd(C,m)=1`, we have `σ(Cm)=σ(C)σ(m)>2Cm`. Thus, a spoof is not a perfect number but an abundant number.

## Search definition

Given an upper bound `B`, the program searches for pairs of positive integers `(C,m)` satisfying:

- `C` is an odd square;
- `m` is composite and `m≡1 (mod 4)`;
- `gcd(C,m)=1`;
- `σ(C)(m+1)=2Cm`;
- `D=Cm<B`.

Solving `σ(C)(m+1)=2Cm` directly gives

```math
m=\frac{\sigma(C)}{2C-\sigma(C)}.
```

Thus, each `C` determines at most one `m`. The search enumerates `C` and requires `2C-\sigma(C)>0` and `2C-\sigma(C)\mid\sigma(C)`.

The example above corresponds to the following program variables:

```math
C=3^2\cdot7^2\cdot11^2\cdot13^2,
\qquad
m=22021=19^2\cdot61,
\qquad
D=Cm=198585576189.
```

For `B=10^12`, the program finds and reports this solution.

## Quick start

### Running from source

The source code is cross-platform. The repository includes a precompiled Windows x64 kernel for CPython 3.11; other platforms require building the corresponding native module. The instructions below use Windows.

```text
.runtime/compiled/descartes_search.cp311-win_amd64.pyd
```

Running from source requires CPython 3.11 and `python-flint`. Create an environment in the project directory:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install python-flint
```

Open the desktop interface:

```powershell
.\start_gui.cmd
```

Alternatively, use the command line:

```powershell
.\run.cmd --help
.\run.cmd --bound 1000000000000 --workers 4 --fresh
```

`--fresh` clears the corresponding checkpoints and starts the search from scratch. If a run is interrupted, resume it with the same bound and output directory:

```powershell
.\run.cmd --bound 1000000000000 --workers 4 --resume
```

If checkpoints already exist, you must specify either `--fresh` or `--resume`; these options are mutually exclusive. With `--resume`, the output directory and all search parameters except the worker count must match those of the checkpoint.

A bound of `10^12` provides a demonstration that runs in seconds. Searches with bounds of `10^60` or higher may take days or longer on consumer hardware; do not use a large bound for your first trial run.

The `10^12` run finds Descartes' example and prints output of the form:

```text
HIT D=198585576189 q=9018009 m=22021 factors=((3, 2), (7, 2), (11, 2), (13, 2))
```

Here, `q` is the program's field name for the square part `C`.

### Building the Windows x64 executable

Install PyInstaller in `.venv`, then run:

```powershell
.\build_desktop.ps1
```

The build output is in `dist_gui/Spoof/`, which contains `Spoof.exe` and `SpoofEngine/`. Copy the entire directory. The build script accepts `-Python`, `-OutputDir`, and `-WorkDir`; the output directory must be empty. The icon comes from `assets/icon.ico`.

## Search workflow

### Search algorithm

Each search node stores the square part selected so far, `A`, together with `S=σ(A)` and the largest prime factor selected so far. A depth-first search (DFS) enumerates prime factors in increasing order, considering their even powers. It prunes branches using necessary conditions derived from upper bounds on abundancy, divisibility constraints, and bounds on the remaining primes.

For each candidate square part `C`, the program computes the corresponding `m` from `C` and `σ(C)` and requires it to be a positive integer. It then checks that `m` is composite, `m≡1 (mod 4)`, `gcd(C,m)=1`, and `Cm<B`. A hit is recorded if all checks pass.

### Parallel execution

The search nodes are grouped into batches according to the split depth and batch size. Each batch ID determines the shard to which the batch belongs, and the batches are processed in parallel by multiple worker processes. Idle workers take over subtrees and prime intervals that other workers have not yet searched, reusing the pruning results already computed at the parent node.

Once all work in a batch is complete, its node count, hit records, and unresolved records are committed together. Checkpoints save progress at the granularity of complete batches.

## Parameters

Common parameters:

| Parameter | Default | Description |
|---|---|---|
| `--bound` | `10^35` | Search bound; must be a full decimal integer |
| `--workers` | `min(16, CPU count)` | Number of parallel worker processes |
| `--output-dir` | `log/<bound>` | Directory for logs, results, and checkpoints |
| `--fresh` | Off | Clear the corresponding checkpoints and restart; mutually exclusive with `--resume` |
| `--resume` | Off | Resume from a matching checkpoint; mutually exclusive with `--fresh` |
| `--shards` | 1 | Total number of logical shards |
| `--owned` | All shards | Shards assigned to this machine, such as `0:1` or `0,2`; zero-based |

Scheduling and tuning parameters:

| Parameter | Default | Description |
|---|---|---|
| `--split-depth` | 4 | Depth at which the search frontier is split |
| `--chunksize` | 16 | Number of subtrees per batch |
| `--prime-cap` | `auto` | Upper limit on primes; `auto` is `max(3, floor(((B-1)/9)^(1/4)))` |
| `--node-cap` | `none` | `none` means no node limit is imposed |
| `--checkpoint-seconds` | 900 | Checkpoint interval |
| `--assist-poll` | 1024 | Assistance polling interval |
| `--no-assist` | Off | Disable dynamic load balancing for baseline comparisons |

The native prime iterator uses 64-bit `primesieve`. At a bound of `10^78`, the `auto` prime cap is approximately `1.8257×10^19`, still below `2^64−1`. At `10^79`, it is approximately `3.2467×10^19`, exceeding `2^64−1`, so the program rejects the run before the search starts. Manually specifying a lower `--prime-cap` allows the run to start, but may leave unresolved branches, in which case the result is `INCOMPLETE`.

## Output and recovery

By default, output is written to `log/<bound>/`:

```text
log/1e60/
  run.log
  result.json
  A.ckpt
  B.ckpt
```

- `run.log`: one JSON event per line, recording startup, progress, checkpoints, work splits for assistance, hits, and completion;
- `result.json`: the authoritative result, including status, counters, batch counts, hit lines, and unresolved records.

To guard against interrupted writes, checkpoints alternate between two files, A and B. Each write replaces the older file, while the other retains the previous complete state. During recovery, the program verifies the CRC32 checksum and the search parameters; if validation fails, recovery falls back to the other file.

`HIT_FOUND` is written as soon as a solution is found and is included in the interface's `Hits found` count. `provisional=true` indicates that the hit may not yet have been saved in a checkpoint; the hit lines in `result.json` determine the final result. `Reported nodes` includes nodes from committed batches, frontier nodes, and nodes from fragments that have already returned within unfinished batches.

A complete search of the specified range requires `status == "COMPLETE"` and zero values for `open_factor`, `open_branch`, and `open_candidates`. If these conditions hold and the hit count is zero, there are no spoofs with composite `m` in that range.

When resuming, keep the bound, prime cap, node cap, split depth, batch size, shard assignment, and output directory unchanged; the worker count may be changed. Batches that were in progress are recomputed in full, so an interruption may require repeating more than one checkpoint interval's worth of work. Each output directory should be used by only one coordinator process.

## Desktop interface and shard merging

Double-click `start_gui.cmd` to open the desktop interface. The interface reads the logs once per second. Closing the window leaves the search process running. Terminating a search stops its entire process tree; the next run can resume from a valid checkpoint.

Offline shards are numbered starting at 0. For example, to distribute four shards across two machines:

```powershell
.\start_search.ps1 -Bound ('1' + ('0' * 35)) -Shards 4 -Owned '0:1' -Workers 12 -Fresh
.\start_search.ps1 -Bound ('1' + ('0' * 35)) -Shards 4 -Owned '2:3' -Workers 12 -Fresh
```

Use **Merge results** on the left side of the interface to add the result JSON files from each machine. Once validation passes, select **Merge and save**. From the command line, use:

```powershell
.\merge_results.ps1 -InputDir collected_results -Output merged.json
```

The merge utility checks each result's search parameters, batch counts, completion status, unresolved cases, and HIT count. It also requires complete shard coverage with no overlapping shard IDs. Results that fail these checks are not written to a merged file.

## Building the kernel

The repository includes a precompiled `.pyd` module, so the program can be run directly. Rebuilding the kernel requires a toolchain with the following components:

- `build_env/Scripts/python.exe` (with Cython and setuptools);
- `w64devkit/bin`;
- `build/primesieve/source/include`;
- `build/primesieve/build/libprimesieve.a`.

```powershell
.\build.ps1 -Toolchain 'D:\path\to\toolchain'
```

Alternatively, set `SPOOF_TOOLCHAIN` and run `.\build.ps1`. Build artifacts are written to `.runtime/compiled/`. Do not recompile or replace the running code while a search is in progress.

## References

Banks, W. D., Güloğlu, A. M., Nevans, C. W., & Saidak, F. (2008). Descartes
numbers. In J.-M. De Koninck, A. Granville, & F. Luca (Eds.), *Anatomy of integers*
(CRM Proceedings & Lecture Notes, Vol. 46, pp. 167–173). American Mathematical Society.
[https://doi.org/10.1090/crmp/046/12](https://doi.org/10.1090/crmp/046/12)

BYU Computational Number Theory Group. (2022). Odd, spoof perfect
factorizations. *Journal of Number Theory, 234*, 31–47.
[https://doi.org/10.1016/j.jnt.2021.07.028](https://doi.org/10.1016/j.jnt.2021.07.028)

Descartes, R. (1638, November 15). Letter to Marin Mersenne [Letter]. In C. Adam
& P. Tannery (Eds.), *Œuvres de Descartes* (1898 edition, Vol. 2, letter CXLIX,
esp. pp. 429–430). Léopold Cerf.
[Digitized text](https://fr.wikisource.org/wiki/Œuvres_de_Descartes/Édition_Adam_et_Tannery/Tome_2/Texte_entier)

Dittmer, S. J. (2014). Spoof odd perfect numbers. *Mathematics of Computation,
83*(289), 2575–2582.
[https://doi.org/10.1090/S0025-5718-2013-02793-7](https://doi.org/10.1090/S0025-5718-2013-02793-7)

Tóth, L. (2021). On the density of spoof odd perfect numbers. *Computational
Methods in Science and Technology, 27*(1), 25–28.
[https://doi.org/10.12921/cmst.2021.0000005](https://doi.org/10.12921/cmst.2021.0000005)

Voight, J. (2003). On the nonexistence of odd perfect numbers. In S. Katok,
A. Sossinsky, & S. Tabachnikov (Eds.), *MASS Selecta: Teaching and Learning
Advanced Undergraduate Mathematics* (pp. 293–300). American Mathematical Society.
[Author's version](https://jvoight.github.io/articles/opn-mass-rev-060211.pdf)

## License and author

MIT License. See [LICENSE](LICENSE).

For third-party components and obligations associated with binary distribution, see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md):
primesieve (BSD-2), python-flint (MIT), FLINT/GMP/MPFR (LGPL), and the GCC Runtime
Library Exception.

Chengyuan Tang
