"""Worker execution and deterministic frontier splitting.

Only worker execution and streaming frontier splitting live here; no progress,
checkpoint or result-merge logic.
"""
from array import array
from collections import deque
import contextlib
import io
import mmap
from multiprocessing.connection import Connection
import os
from pathlib import Path
import select
import socket

import descartes_search as core

COUNTERS = ("nodes", "hits", "open_factor", "open_branch", "open_candidates")
_prime_file = _prime_map = _primes = None
_parameters = None

def default_workers():
    if not hasattr(os, "sched_getaffinity"):
        return min(16, os.cpu_count() or 1)
    count = len(os.sched_getaffinity(0))
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()
        if quota != "max":
            count = min(count, max(1, int(quota) // int(period)))
    except (OSError, ValueError):
        pass
    return max(1, count)


def prime_cache(cap):
    cap = min(cap, core.PRIME_TABLE_CAP)
    directory = Path(__file__).resolve().parent
    if directory.name == "compiled" and directory.parent.name == ".runtime":
        directory = directory.parent.parent
    directory /= ".prime_cache"
    directory.mkdir(exist_ok=True)
    path = directory / f"primes_{cap}.u32"
    if not path.exists():
        temporary = path.with_suffix(f".{os.getpid()}.tmp")
        with temporary.open("wb") as output:
            array("I", core.odd_primes(cap)).tofile(output)
        temporary.replace(path)
    return path


def attach_primes(path):
    global _prime_file, _prime_map, _primes
    _prime_file = open(path, "rb")
    _prime_map = mmap.mmap(_prime_file.fileno(), 0, access=mmap.ACCESS_READ)
    _primes = core.prime_view(memoryview(_prime_map).cast("I"))


class SplitSearch(core.Search):
    def __init__(self, bound, prime_cap, node_cap, depth, chunksize, submit):
        super().__init__(bound, prime_cap, node_cap, _primes)
        self.depth = depth
        self.chunksize = chunksize
        self.submit = submit
        self.batch = []

    def visit(self, a, c, last, factors, sigma_factors=None, new_sigma=1):
        if self.stopped or self.nodes >= self.node_cap:
            self.stopped = True
            return
        # Let the kernel count and prune already abundant prefixes.
        if len(factors) >= self.depth and 2 * a > c:
            self.batch.append((a, c, last, factors, sigma_factors, new_sigma))
            if len(self.batch) == self.chunksize:
                batch, self.batch = self.batch, []
                self.submit(batch)
            return
        super().visit(a, c, last, factors, sigma_factors, new_sigma)


def initialize_worker(path, parameters):
    global _parameters
    attach_primes(path)
    _parameters = parameters


class _HitTee(io.StringIO):
    """StringIO that forwards every complete HIT line the moment it is written."""

    def __init__(self, notify):
        super().__init__()
        self._notify = notify
        self._tail = ''
        self._warned = False

    def write(self, text):
        super().write(text)
        self._tail += text
        while '\n' in self._tail:
            line, self._tail = self._tail.split('\n', 1)
            line = line.rstrip('\r')
            if line.startswith('HIT '):
                try:
                    self._notify(line)
                except Exception as error:
                    if not self._warned:
                        self._warned = True
                        try:
                            import sys
                            print(f'LOG_WARNING live HIT notification failed ({type(error).__name__}); final evidence retained',
                                  file=sys.stderr, flush=True)
                        except Exception:
                            pass  # Diagnostic failure must not abort computation.
        return len(text)


def search_batch(tasks, should_yield=None, assist_poll=1024, notify_hit=None):
    search = core.Search(*_parameters, _primes)
    if should_yield is not None:
        search.assist_check = should_yield
        search.assist_poll = assist_poll
    output = _HitTee(notify_hit) if notify_hit is not None else io.StringIO()
    totals = [0] * len(COUNTERS)
    limited = False
    with contextlib.redirect_stdout(output):
        for index, task in enumerate(tasks):
            # Each original root task, including a continuation, owns an
            # independent accounting fragment. No node-local work is rerun
            # when executing a continuation.
            search.nodes = search.hits = 0
            search.open_factor = search.open_branch = search.open_candidates = 0
            search.stopped = False
            try:
                if len(task) == 11 and task[0] == 'C':
                    search.visit_continuation(task)
                elif len(task) == 2 and task[0] == 'T':
                    search.visit(*task[1])
                else:
                    search.visit(*task)
            except core.SearchYield as yielded:
                # The thrown fragment list covers everything not yet visited
                # inside the current task. Append unstarted batch siblings.
                rest = list(yielded.remaining)
                rest.extend(('T', later) if len(later) == 6 else later
                            for later in tasks[index+1:])
                totals = [x + getattr(search, name)
                          for x, name in zip(totals, COUNTERS)]
                return ('YIELD', (tuple(totals), limited, output.getvalue(),
                                  search.unresolved), rest)
            totals = [x + getattr(search, name)
                      for x, name in zip(totals, COUNTERS)]
            limited |= search.stopped
    return ('DONE', (tuple(totals), limited, output.getvalue(),
                     len(tasks), search.unresolved))


def search_worker(sock, path, parameters, request_token, assist_poll, assist_enabled,
                  notify_hits):
    with Connection(sock.detach()) as connection:
        try:
            initialize_worker(path, parameters)
        except Exception as error:
            connection.send(error)
            return
        connection.send(None)

        def should_yield():
            return bool(request_token.value)

        def notify_hit(line):
            # Out-of-band progress message; the coordinator must not treat it as
            # the job result and must keep this connection in `pending`.
            connection.send(('HIT', line))

        while True:
            message = connection.recv()
            if message is None:
                return
            if not isinstance(message, tuple) or len(message) != 2 or message[0] != 'RUN':
                connection.send(RuntimeError('invalid worker job'))
                return
            try:
                result = search_batch(message[1],
                                      should_yield if assist_enabled else None,
                                      assist_poll, notify_hit if notify_hits else None)
            except Exception as error:
                connection.send(error)
                return
            connection.send(result)


class WorkerPool:
    def __init__(self, context, workers, path, parameters, assist_poll=1024,
                 assist_enabled=True, notify_hits=False):
        self.processes = []
        self.connections = []
        self.idle = deque()
        self.request_tokens = {}
        self.connection_processes = {}
        try:
            for _ in range(workers):
                parent, child = socket.socketpair()
                with parent, child:
                    for sock in (parent, child):
                        if sock.family in (socket.AF_INET, socket.AF_INET6):
                            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    connection = Connection(parent.detach())
                    self.connections.append(connection)
                    token = context.Value('Q', 0)
                    self.request_tokens[connection] = token
                    process = context.Process(
                        target=search_worker,
                        args=(child, path, parameters, token, assist_poll,
                              assist_enabled, notify_hits)
                    )
                    self.processes.append(process)
                    self.connection_processes[connection] = process
                    process.start()
            waiting = set(self.connections)
            while waiting:
                for connection in select.select(waiting, [], [])[0]:
                    result = connection.recv()
                    if isinstance(result, BaseException):
                        raise result
                    waiting.remove(connection)
                    self.idle.append(connection)
        except BaseException:
            self.close(abort=True)
            raise

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        self.close(abort=exc_type is not None or len(self.idle) != len(self.connections))

    def submit(self, batch, connection=None):
        if connection is None:
            connection = self.idle.popleft()
        connection.send(('RUN', batch))

    def set_request(self, connection, token):
        self.request_tokens[connection].value = token

    def clear_request(self, connection):
        self.request_tokens[connection].value = 0

    def close(self, abort=False):
        error = None
        if not abort and len(self.idle) != len(self.connections):
            error = RuntimeError("Search worker results have not been collected")
            abort = True
        try:
            if not abort:
                for connection in self.connections:
                    try:
                        connection.send(None)
                    except OSError as failure:
                        error = failure
                        abort = True
                        break
        finally:
            try:
                for process in self.processes:
                    if process.pid is not None and abort and process.is_alive():
                        process.terminate()
                for process in self.processes:
                    if process.pid is not None:
                        process.join()
            finally:
                for connection in self.connections:
                    connection.close()
        if error is not None:
            raise RuntimeError("Search worker shutdown failed") from error
        if not abort and any(process.exitcode != 0 for process in self.processes):
            raise RuntimeError("Search worker exited abnormally")


