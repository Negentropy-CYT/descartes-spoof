#!/usr/bin/env python3
"""Exact branch search for classical Descartes numbers D < B.

Exact integer arithmetic with certified prime factorization.

Run: python3 descartes_search.py --bound 1000000000000 --prime-cap 10000
Only COMPLETE certifies full coverage. Resource limits produce INCOMPLETE.
"""

from __future__ import annotations

import argparse
import sys
from array import array
from bisect import bisect_right, bisect_left
from functools import lru_cache
from math import gcd, isqrt

import cython

if cython.compiled:
    from cython.cimports._primesieve import (
        primesieve_iterator,
        primesieve_init,
        primesieve_free_iterator,
        primesieve_jump_to,
        primesieve_next_prime,
        uint64_t,
    )

try:
    from flint import fmpz
except ImportError:
    fmpz = None


MR64 = (2, 325, 9375, 28178, 450775, 9780504, 1795265022)
FACTOR_TRIAL_CAP = 10000
DEFAULT_BOUND = 10**35
DEFAULT_PRIME_CAP = 400000000
DEFAULT_NODE_CAP = 10**18
# Compiled searches keep only the trial-division table; Python keeps its full table.
PRIME_TABLE_CAP = FACTOR_TRIAL_CAP if cython.compiled else 2**32 - 1
PRIME_CAP_LIMIT = 2**64 - 1 if cython.compiled else 2**32


def primality(n):
    """Return 'prime', 'composite', or 'unknown', with proven prime results."""
    if n < 2:
        return "composite"
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return "prime" if n == p else "composite"
    if n >= 1 << 64 and fmpz is not None:
        return "prime" if fmpz(n).is_prime() else "composite"
    d, s = n - 1, 0
    while not d & 1:
        d >>= 1
        s += 1
    for a in MR64 if n < 1 << 64 else (2, 3, 5, 7, 11, 13, 17):
        a %= n
        if a == 0:
            continue
        x = pow(a, d, n)
        if x == 1 or x == n - 1:
            continue
        for _ in range(s - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return "composite"
    return "prime" if n < 1 << 64 else "unknown"


def odd_primes(cap):
    sieve = bytearray(b"\x01") * (cap + 1)
    for p in range(3, isqrt(cap) + 1, 2):
        if sieve[p]:
            sieve[p * p :: 2 * p] = b"\x00" * (((cap - p * p) // (2 * p)) + 1)
    return [p for p in range(3, cap + 1, 2) if sieve[p]]


def prime_view(primes: cython.const_uint[::1]):
    """Use a read-only native view in Cython and the buffer directly in Python."""
    return primes


def trial64(
    rem: cython.ulonglong,
    primes: cython.const_uint[::1],
    stop: cython.uint,
):
    """Return distinct prime factors and the remainder by exact 64-bit division."""
    index: cython.Py_ssize_t
    p: cython.uint
    square: cython.ulonglong
    found = []
    for index in range(len(primes)):
        p = primes[index]
        if p > stop:
            break
        # Widen before multiplying; this also works without Cython in Python.
        square = p
        square *= p
        if square > rem:
            if rem > 1:
                found.append(rem)
            rem = 1
            break
        if rem % p == 0:
            while rem % p == 0:
                rem //= p
            found.append(p)
    return found, rem


def brent_factor(n):
    """Find an exact proper divisor, or return None after bounded attempts."""
    if n % 2 == 0:
        return 2
    for constant in (1, 3, 5):
        y, length, divisor = 2, 1, 1
        while divisor == 1 and length <= 1 << 18:
            x = y
            for _ in range(length):
                y = (y * y + constant) % n
            offset = 0
            while offset < length and divisor == 1:
                saved = y
                product = 1
                count = min(128, length - offset)
                for _ in range(count):
                    y = (y * y + constant) % n
                    product = product * abs(x - y) % n
                divisor = gcd(product, n)
                offset += count
            length *= 2
        if divisor == n:
            for _ in range(128):
                saved = (saved * saved + constant) % n
                divisor = gcd(abs(x - saved), n)
                if divisor > 1:
                    break
        if 1 < divisor < n:
            return divisor
    return None


@lru_cache(maxsize=8192)
def factor_large(n):
    """Return proven prime factors and an unresolved remainder."""
    if n == 1:
        return (), 1
    if fmpz is not None and n < 1 << 64:
        # Use the existing native backend for machine-sized inputs. Verify
        # both prime certificates and exact division before accepting factors.
        found, remaining = [], n
        for prime, exponent in fmpz(n).factor():
            p, e = int(prime), int(exponent)
            if p < 2 or e < 1 or not prime.is_prime():
                return (), n
            power = p**e
            if remaining % power:
                return (), n
            remaining //= power
            found.extend([p] * e)
        return tuple(found), remaining
    if primality(n) == "prime":
        return (n,), 1
    root = isqrt(n)
    divisor = root if root * root == n else brent_factor(n)
    if divisor is None:
        if fmpz is not None:
            factors = fmpz(n).factor()
            if all(p.is_prime() for p, _ in factors):
                return tuple(int(p) for p, e in factors for _ in range(e)), 1
        return (), n
    left, left_rem = factor_large(divisor)
    right, right_rem = factor_large(n // divisor)
    return left + right, left_rem * right_rem


def square_obligations(a, c, known_primes):
    """Precompute certified q-powers outside the fixed core A.

    known_primes contains distinct certified primes, possibly an incomplete
    factorization of c. Unknown factors are deliberately ignored.
    """
    obligations = []
    for q in known_primes:
        if a % q == 0:
            continue
        residual, exponent = c, 0
        while residual % q == 0:
            residual //= q
            exponent += 1
        if exponent:
            obligations.append((q, q**exponent, q**((exponent + 1) // 2)))
    return obligations


def remaining_square_limit(bound, a, minimum_m, forced, obligations):
    """Bound R=u^2 using the certified divisibility S | u*m.

    A full q-power is used only when q is already forced into m; otherwise
    its exponent is rounded up after division by two. All arithmetic is exact.
    """
    required = 1
    for q, full_power, root_power in obligations:
        required *= full_power if forced % q == 0 else root_power
    root_limit = (bound - 1) // a // required
    return min((bound - 1) // minimum_m // a, root_limit * root_limit)


@cython.cfunc
@cython.inline
def possible(p, limit, numerator, denominator) -> cython.bint:
    p2 = p * p
    if p2 * p2 > limit:
        return False
    x = limit
    count: cython.Py_ssize_t = 0
    while x >= p2:
        x //= p2
        count += 1
    return numerator * pow(p, count) > denominator * pow(p - 1, count)


class SearchYield(Exception):
    """Cooperative return of disjoint, as-yet-unsearched DFS fragments."""

    def __init__(self, remaining):
        self.remaining = remaining
        super().__init__('search yielded remaining work')


@cython.cclass
class Search:
    bound = cython.declare(object, visibility="public")
    cap = cython.declare(object, visibility="public")
    node_cap = cython.declare(object, visibility="public")
    primes = cython.declare(object, visibility="public")
    nodes = cython.declare(object, visibility="public")
    open_factor = cython.declare(object, visibility="public")
    unresolved = cython.declare(object, visibility="public")
    open_branch = cython.declare(object, visibility="public")
    open_candidates = cython.declare(object, visibility="public")
    hits = cython.declare(object, visibility="public")
    stopped = cython.declare(cython.bint, visibility="public")
    # Optional per-worker depth slot. It is instrumentation only.
    depth_slot = cython.declare(cython.int[::1], visibility="public")
    # Optional idle-worker assistance. It does not alter any mathematical test.
    assist_check = cython.declare(object, visibility="public")
    assist_tick = cython.declare(cython.uint, visibility="public")
    assist_poll = cython.declare(cython.uint, visibility="public")

    def __init__(self, bound, prime_cap, node_cap, primes=None):
        self.bound = bound
        self.cap = prime_cap
        self.node_cap = node_cap
        if primes is None:
            table_cap = min(prime_cap, PRIME_TABLE_CAP)
            primes = prime_view(array("I", odd_primes(table_cap)))
        self.primes = primes
        self.depth_slot = array("i", [-1])
        self.nodes = 0
        self.open_factor = 0
        self.unresolved = []
        self.open_branch = 0
        self.open_candidates = 0
        self.stopped = False
        self.hits = 0
        self.assist_check = None
        self.assist_tick = 0
        self.assist_poll = 1024

    @cython.ccall
    def completion(self, a, p, e, pp, delta, certified, factors):
        q = a * pp
        if delta <= 0 or q % delta:
            return
        m = 2 * (q // delta) - 1
        if m <= 1 or m % 4 != 1 or gcd(q, m) != 1 or q * m >= self.bound:
            return
        if not certified:
            state = primality(p)
            if state == "composite":
                return
            if state == "unknown":
                self.open_candidates += 1
                print(f"UNRESOLVED primality(p) p={p} e={e} q={q} m={m}", flush=True)
                return
        state = primality(m)
        if state == "prime":
            return
        if state == "unknown":
            self.open_candidates += 1
            print(f"UNRESOLVED primality(m) p={p} e={e} q={q} m={m}", flush=True)
            return
        self.hits += 1
        print(
            f"HIT D={q * m} q={q} m={m} factors={factors + ((p, 2 * e),)}",
            flush=True,
        )

    @cython.ccall
    def finish_prime(self, a, c, p, limit, factors, sigma_factor):
        """Enumerate terminal powers; sigma factors have certified primality."""
        p2 = p * p
        pp, offset = p2, c * (p + 1)
        delta = (2 * a - c) * pp - offset
        e = 1
        while pp <= limit and delta > 0 and (sigma_factor or delta <= a):
            self.completion(a, p, e, pp, delta, sigma_factor, factors)
            delta = p2 * delta - offset
            pp *= p2
            e += 1

    @cython.ccall
    def visit(self, a, c, last, factors, sigma_factors=None, new_sigma=1):
        primes: cython.const_uint[::1]
        prime_index: cython.Py_ssize_t
        prime_count: cython.Py_ssize_t
        supplier: primesieve_iterator
        next_prime: uint64_t
        supplier_ready: cython.bint = False

        if self.nodes >= self.node_cap:
            self.stopped = True
            return
        depth: cython.int = len(cython.cast(tuple, factors))
        self.depth_slot[0] = depth
        self.nodes += 1
        t = 2 * a - c
        if t <= 0:  # sigma(q)/q increases under every extension
            return
        # With final core q=a*r, c=sigma(a) divides a*r*m. The selected
        # prime exponents in a are fixed and gcd(a,r*m)=1. Thus all copies
        # of selected primes in c must fit in a, and c/gcd(a,c) divides r*m.
        selected = gcd(a, c)
        required = c // selected
        lcm_prefix = a * required
        if lcm_prefix >= self.bound or (selected != 1 and gcd(required, selected) != 1):
            return
        if (sigma_factors is not None and factors and factors[-1][1] == 2
                and lcm_prefix * (last + 2) >= self.bound):
            # In any strict extension, each later prime r must satisfy r^2 | c:
            # lcm(A,c) divides D, but D/lcm(A,c) < last+2 <= r and r does not divide A.
            # Its square exceeds last^2+last+1, so r also divides the parent sigma.
            for parent_prime in sigma_factors:
                if parent_prime > last and c % (parent_prime * parent_prime) == 0:
                    break
            else:
                return
        # gcd(m+1,A) must fit the selected-prime exponent budget left by c.
        remaining_selected = a // selected

        primes = self.primes
        prime_count = len(primes)
        k = c // t
        # A new prime p <= k makes sigma(A*p^2) > 2*A*p^2.
        # Higher powers and later factors only increase abundancy.
        first_child = max(last, k) + 1
        minimum_m = max(9, 4 * ((k + 3) // 4) + 1)
        # gcd(m,A)=1 and m is composite. Its prime factors are all at
        # least the first odd prime not dividing A, so m >= that prime^2.
        r = 3
        for prime_index in range(prime_count):
            r = primes[prime_index]
            if a % r:
                minimum_m = max(minimum_m, r * r)
                break

        # Factor the newest sigma component before using its forced divisors.
        limit = (self.bound - 1) // minimum_m // a
        max_p = isqrt(limit)
        if max_p < first_child:
            return
        # Reuse complete parent factors of sigma(A), factoring only the new
        # prime-power component. Fall back to the whole sigma when unresolved.
        rem = c if sigma_factors is None else new_sigma
        sigma_primes = [] if sigma_factors is None else list(sigma_factors)
        tested_through = min(self.cap, max_p, FACTOR_TRIAL_CAP)
        if rem < 1 << 64:
            found, rem = trial64(rem, primes, tested_through)
            sigma_primes.extend(found)
        else:
            # Larger values retain Python's arbitrary-precision arithmetic.
            for prime_index in range(prime_count):
                p = primes[prime_index]
                if p > tested_through:
                    break
                if p * p > rem:
                    if rem > 1:
                        sigma_primes.append(rem)
                    rem = 1
                    break
                if rem % p == 0:
                    while rem % p == 0:
                        rem //= p
                    sigma_primes.append(p)
        if rem > 1 and max_p > tested_through:
            if isqrt(rem) <= tested_through:
                sigma_primes.append(rem)
                rem = 1
            else:
                found, rem = factor_large(rem)
                sigma_primes.extend(found)
                if rem > 1:
                    self.open_factor += 1
                    self.unresolved.append(rem)
        sigma_primes = tuple(sorted(set(sigma_primes)))
        # Proven factors stay reusable even when larger than the prime table.
        next_sigma = sigma_primes if rem == 1 else None
        obligations = square_obligations(a, c, sigma_primes)
        # A known prime q < first_child absent from A cannot enter Q later.
        # sigma(A) divides sigma(Q), so q^v_q(C) must divide m.
        forced = 1
        if sigma_primes is not None:
            for q in sigma_primes:
                if q >= first_child:
                    break
                if a % q:
                    residual = c
                    while residual % q == 0:
                        residual //= q
                        forced *= q
        # Consume inherited factors from the largest down as the bound tightens.
        factor_index = len(sigma_primes) - 1 if sigma_primes is not None else -1
        while True:
            # If the forced part is prime, a composite m needs another prime
            # factor. Every such factor is at least r by gcd(m,A)=1.
            if forced in sigma_primes:
                minimum_m = max(minimum_m, forced * r)
            if forced > 1:
                # F*k == 1 (mod 4) exactly when k == F (mod 4).
                multiple = (minimum_m + forced - 1) // forced
                multiple += (forced - multiple) % 4
                minimum_m = forced * multiple

            step = 4 * forced
            while (gcd(minimum_m, a) != 1
                   or remaining_selected % gcd(minimum_m + 1, a)):
                minimum_m += step

            # Recompute after each newly forced factor. This only decreases
            # the remaining-core budget and can force further factors into m.
            limit = remaining_square_limit(
                self.bound, a, minimum_m, forced, obligations
            )
            max_p = isqrt(limit)
            if max_p < 3 or max_p < first_child:
                return
            if factor_index < 0 or sigma_primes[factor_index] <= max_p:
                break
            while factor_index >= 0 and sigma_primes[factor_index] > max_p:
                q = sigma_primes[factor_index]
                residual = c
                while residual % q == 0:
                    residual //= q
                    forced *= q
                factor_index -= 1

        # For p not dividing C, delta is coprime to p and must divide A.
        # If f(p)=delta>0, then f(p+1)-f(p)>C>=A. Moreover f(p)<0
        # for p<=C/T and f(p)>0 for p>=floor(C/T)+2.
        # Hence delta in (0,A] is possible only at p0 or p0+1.
        p0 = k + 1
        # Only one of these consecutive candidates is odd.
        p = p0 | 1
        if p > last and p >= 3 and p <= max_p and c % p:
            self.finish_prime(a, c, p, limit, factors, False)

        numerator, denominator = c * (minimum_m + 1), 2 * minimum_m * a
        # No exceptional final prime and no possible recursive extension.
        completion_cap = min(max_p, (2 * minimum_m * a - 1) // (minimum_m * t - c))
        if completion_cap <= last and not possible(
            last + 2, limit, numerator, denominator
        ):
            return

        for p in sigma_primes:
            if last < p <= completion_cap:
                self.finish_prime(a, c, p, limit, factors, True)

        # With r remaining primes >=p, I(final)<(C/A)*(p/(p-1))^r;
        # I(final)>=2*M/(M+1). This exact integer test is necessary.
        if possible(max(self.cap + 1, last + 2), limit, numerator, denominator):
            self.open_branch += 1
        # Preserve the original native hot path when assistance is explicitly
        # disabled. No continuation tuple, helper calls, or IPC polling.
        if self.assist_check is None:
            prime_index = bisect_left(self.primes, first_child)
            try:
                while True:
                    if prime_index < prime_count:
                        p = primes[prime_index]
                        prime_index += 1
                    else:
                        if not cython.compiled:
                            break
                        if not supplier_ready:
                            start = max(3, first_child, PRIME_TABLE_CAP + 1)
                            if start > self.cap or not possible(
                                start, limit, numerator, denominator
                            ):
                                break
                            primesieve_init(cython.address(supplier))
                            supplier_ready = True
                            primesieve_jump_to(cython.address(supplier), start, self.cap)
                        next_prime = primesieve_next_prime(cython.address(supplier))
                        if supplier.is_error:
                            raise RuntimeError("primesieve generation failed")
                        p = next_prime
                        if p > self.cap:
                            break
                    if not possible(p, limit, numerator, denominator):
                        break
                    p2 = p * p
                    pp, sp = p2, 1 + p + p2
                    p_next_sq = (p + 2) * (p + 2)
                    e = 1
                    while pp * p_next_sq <= limit:
                        # Selected prime exponents cannot increase in a completion.
                        # Any overlap contributed by sp must fit the remaining budget.
                        if selected == 1 or remaining_selected % gcd(sp, a) == 0:
                            self.visit(
                                a * pp, c * sp, p,
                                factors + ((p, 2 * e),), next_sigma, sp
                            )
                            self.depth_slot[0] = depth
                        if self.stopped:
                            return
                        sp = p2 * sp + p + 1
                        pp *= p2
                        e += 1
            finally:
                if cython.compiled and supplier_ready:
                    primesieve_free_iterator(cython.address(supplier))
            return
        self.scan_children(
            a, c, last, factors, sigma_factors, new_sigma,
            limit, numerator, denominator, selected, remaining_selected,
            next_sigma, first_child, 1, min(self.cap, isqrt(isqrt(limit)))
        )

    @cython.ccall
    def scan_children(self, a, c, last, factors, sigma_factors, new_sigma,
                      limit, numerator, denominator,
                      selected, remaining_selected, next_sigma,
                      start_p, start_e, stop_p):
        """Resume *only* descendant enumeration; node-local effects are done.

        A continuation stores the prepared loop values, so visit()'s node
        counter, factor work, open records, completion(), and open_branch are
        never repeated on resumed fragments.
        """
        primes: cython.const_uint[::1] = self.primes
        prime_index: cython.Py_ssize_t = bisect_left(self.primes, start_p)
        prime_count: cython.Py_ssize_t = len(primes)
        supplier: primesieve_iterator
        next_prime: uint64_t
        supplier_ready: cython.bint = False
        depth: cython.int = len(cython.cast(tuple, factors))
        self.depth_slot[0] = depth
        if start_p > stop_p:
            return
        try:
            while True:
                if prime_index < prime_count:
                    p = primes[prime_index]
                    prime_index += 1
                else:
                    if not cython.compiled:
                        break
                    if not supplier_ready:
                        start = max(3, start_p, PRIME_TABLE_CAP + 1)
                        if start > stop_p or not possible(
                            start, limit, numerator, denominator
                        ):
                            break
                        primesieve_init(cython.address(supplier))
                        supplier_ready = True
                        primesieve_jump_to(cython.address(supplier), start, stop_p)
                    next_prime = primesieve_next_prime(cython.address(supplier))
                    if supplier.is_error:
                        raise RuntimeError('primesieve generation failed')
                    p = next_prime
                if p > stop_p:
                    break
                if not possible(p, limit, numerator, denominator):
                    break
                # A prime-range yield works even when almost no child can
                # recurse (the heavy path is candidate scanning itself).
                if self._should_yield():
                    remainder = self._split_remaining(
                        (a, c, last, factors, sigma_factors, new_sigma),
                        limit, numerator, denominator, selected,
                        remaining_selected, next_sigma, p,
                        start_e if p == start_p else 1, stop_p)
                    if len(remainder) >= 2:
                        raise SearchYield(remainder)
                p2 = p * p
                pp, sp = p2, 1 + p + p2
                p_next_sq = (p + 2) * (p + 2)
                e = 1
                # Restoring within a prime: skip exponents that were already
                # fully processed by the original Worker.
                if p == start_p and start_e > 1:
                    e = start_e
                    pp = pow(p, 2 * e)
                    sp = (pow(p, 2 * e + 1) - 1) // (p - 1)
                while pp * p_next_sq <= limit:
                    if selected == 1 or remaining_selected % gcd(sp, a) == 0:
                        if self._should_yield():
                            rest = self._split_remaining(
                                (a, c, last, factors, sigma_factors, new_sigma),
                                limit, numerator, denominator, selected,
                                remaining_selected, next_sigma, p, e + 1,
                                stop_p)
                            if rest:
                                child = (a * pp, c * sp, p,
                                         factors + ((p, 2 * e),), next_sigma, sp)
                                raise SearchYield([('T', child)] + rest)
                        try:
                            self.visit(
                                a * pp, c * sp, p,
                                factors + ((p, 2 * e),), next_sigma, sp
                            )
                        except SearchYield as y:
                            self.depth_slot[0] = depth
                            y.remaining.extend(self._split_remaining(
                                (a, c, last, factors, sigma_factors, new_sigma),
                                limit, numerator, denominator, selected,
                                remaining_selected, next_sigma, p, e + 1,
                                stop_p))
                            raise
                        self.depth_slot[0] = depth
                    if self.stopped:
                        return
                    sp = p2 * sp + p + 1
                    pp *= p2
                    e += 1
        finally:
            if cython.compiled and supplier_ready:
                primesieve_free_iterator(cython.address(supplier))

    @cython.cfunc
    @cython.inline
    def _should_yield(self) -> cython.bint:
        if self.assist_check is None:
            return False
        self.assist_tick += 1
        if self.assist_tick < self.assist_poll:
            return False
        self.assist_tick = 0
        return bool(self.assist_check())

    @cython.ccall
    def _split_remaining(self, node, limit, numerator, denominator,
                         selected, remaining_selected, next_sigma,
                         start_p, start_e, stop_p):
        """Create disjoint continuations, with no parent-node recounting.

        The current prime's remaining exponents are one fragment. All later
        prime candidates are divided into two integer ranges, even when no
        recursion occurs, so workers can help with expensive candidate loops.
        """
        result = []
        if start_e == 1:
            later = start_p
        else:
            p_next_sq = (start_p + 2) ** 2
            if pow(start_p, 2 * start_e) * p_next_sq <= limit:
                result.append(('C', node, limit, numerator, denominator, selected, remaining_selected, next_sigma, start_p, start_e, start_p))
            later = start_p + 1
        if later <= stop_p:
            middle = (later + stop_p) // 2
            result.append(('C', node, limit, numerator, denominator, selected, remaining_selected, next_sigma, later, 1, middle))
            if middle < stop_p:
                result.append(('C', node, limit, numerator, denominator, selected, remaining_selected, next_sigma, middle + 1, 1, stop_p))
        return result

    @cython.ccall
    def visit_continuation(self, task):
        if len(task) != 11 or task[0] != 'C':
            raise ValueError('invalid search continuation')
        (_, node, limit, numerator, denominator, selected,
         remaining_selected, next_sigma, start_p, start_e, stop_p) = task
        a, c, last, factors, sigma_factors, new_sigma = node
        self.scan_children(a, c, last, factors, sigma_factors, new_sigma,
                           limit, numerator, denominator, selected,
                           remaining_selected, next_sigma,
                           start_p, start_e, stop_p)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bound", type=int, default=DEFAULT_BOUND, help="search D < bound")
    parser.add_argument("--prime-cap", type=int, default=DEFAULT_PRIME_CAP)
    parser.add_argument("--node-cap", type=int, default=DEFAULT_NODE_CAP)
    args = parser.parse_args()
    if (
        args.bound < 2
        or not 3 <= args.prime_cap < PRIME_CAP_LIMIT
        or args.node_cap < 1
    ):
        parser.error("invalid search limit")
    search = Search(args.bound, args.prime_cap, args.node_cap)
    search.visit(1, 1, 1, ())
    incomplete = (
        search.stopped
        or search.open_factor
        or search.open_branch
        or search.open_candidates
    )
    status = "INCOMPLETE" if incomplete else "COMPLETE"
    print(
        f"{status} D<{args.bound} nodes={search.nodes} hits={search.hits} "
        f"open_factor={search.open_factor} open_branch={search.open_branch} "
        f"open_candidates={search.open_candidates} "
        f"node_cap_reached={search.stopped}"
    )
    return 0 if status == "COMPLETE" else 2


if __name__ == "__main__":
    sys.exit(main())
