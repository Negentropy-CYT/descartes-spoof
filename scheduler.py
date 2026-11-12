"""Streaming search scheduler with offline shards and 15-minute checkpoints.

No database, no alternate mathematical core, no recursive-state snapshots.
"""
from __future__ import annotations
import argparse
from collections import deque
from bisect import bisect_left
import contextlib
import json
import multiprocessing as mp
import os
from math import isqrt
from pathlib import Path
import select
import time

import descartes_search as core
import workers as base
import checkpoint
from run_log import RunLog

COUNTERS = base.COUNTERS
ASSIST_SPLIT_COOLDOWN = 2.0  # seconds; avoid immediately re-splitting the same donor

# The pure-Python backend materializes the entire prime table in memory.
# The native kernel instead caches 10,000 primes and streams higher
# primes using primesieve. Never silently truncate the mathematical bound.
PYTHON_AUTO_SIEVE_LIMIT = 10_000_000


def automatic_prime_cap(bound):
    """Cover every recursively extensible prime without an arbitrary cutoff.

    In the search core, possible(p, limit, ...) requires p**4 <= limit,
    and every call has limit <= (bound - 1)//(minimum_m*a) <= (bound-1)//9.
    The separate terminal-candidate paths do not depend on prime_cap.
    """
    return max(3, isqrt(isqrt((bound - 1) // 9)))


def resolve_caps(args):
    """Resolve optional CLI limits to integers supported by the search core.

    The core expects an integer node_cap. A bound+1 sentinel is unreachable:
    each visited prefix is a unique positive square component < bound, so
    fewer than bound+1 different nodes can be visited.
    """
    raw_prime = str(args.prime_cap).strip().lower()
    raw_node = str(args.node_cap).strip().lower()
    if raw_prime == 'auto':
        prime_cap = automatic_prime_cap(args.bound)
        prime_mode = 'auto'
    else:
        try:
            prime_cap = int(raw_prime)
        except ValueError as exc:
            raise ValueError('prime-cap must be auto or a positive integer') from exc
        prime_mode = 'manual'

    if raw_node in ('none', 'auto', 'unlimited'):
        node_cap = args.bound + 1
        node_mode = 'unlimited'
    else:
        try:
            node_cap = int(raw_node)
        except ValueError as exc:
            raise ValueError('node-cap must be none or a positive integer') from exc
        node_mode = 'manual'

    if prime_cap < 3:
        raise ValueError('prime-cap must be >= 3')
    if node_cap < 1:
        raise ValueError('node-cap must be positive')
    if not cython_native_backend() and prime_cap > PYTHON_AUTO_SIEVE_LIMIT:
        raise ValueError(
            f'pure-Python backend would materialize a prime table through {prime_cap:,}; '
            f'safe development limit is {PYTHON_AUTO_SIEVE_LIMIT:,}. '
            'Run on Windows with the bundled Cython .pyd for full coverage, '
            'or use --prime-cap N for an explicitly capped test '
            '(which may report INCOMPLETE).'
        )
    if prime_cap >= core.PRIME_CAP_LIMIT:
        raise ValueError(
            f'prime_cap={prime_cap} exceeds the active prime iterator range '
            f'(required prime_cap < {core.PRIME_CAP_LIMIT}); cannot certify COMPLETE'
        )
    args.prime_cap = prime_cap
    args.node_cap = node_cap
    args.prime_cap_mode = prime_mode
    args.node_cap_mode = node_mode
    return args


def cython_native_backend():
    return Path(core.__file__).suffix.lower() in ('.pyd', '.so')


class CompletedRanges:
    """Compressed intervals of completed local batch IDs, independent of total work."""
    def __init__(self, ranges=()):
        self.ranges = [list(r) for r in ranges]
        if any(len(r) != 2 or not all(isinstance(v, int) for v in r)
               or r[0] < 0 or r[0] > r[1] for r in self.ranges):
            raise ValueError('invalid completed ranges')
        if any(left[1] >= right[0] - 1 for left, right in zip(self.ranges, self.ranges[1:])):
            raise ValueError('noncanonical overlapping completed ranges')

    def contains(self, number):
        starts = [r[0] for r in self.ranges]
        at = bisect_left(starts, number)
        if at < len(starts) and starts[at] == number:
            return True
        return at > 0 and self.ranges[at - 1][1] >= number

    def add(self, number):
        if self.contains(number):
            raise ValueError('duplicate batch completion')
        ranges = self.ranges
        at = bisect_left([r[0] for r in ranges], number)
        left = at > 0 and ranges[at - 1][1] + 1 == number
        right = at < len(ranges) and ranges[at][0] - 1 == number
        if left and right:
            ranges[at - 1][1] = ranges[at][1]
            ranges.pop(at)
        elif left:
            ranges[at - 1][1] = number
        elif right:
            ranges[at][0] = number
        else:
            ranges.insert(at, [number, number])

    def count(self):
        return sum(end - start + 1 for start, end in self.ranges)


def parse_owned(spec, shard_count):
    if not spec:
        return list(range(shard_count))
    owned = []
    for part in spec.split(','):
        chunk = part.strip().split(':')
        if len(chunk) == 1:
            owned.append(int(chunk[0]))
        elif len(chunk) == 2:
            start, stop = map(int, chunk)
            owned.extend(range(start, stop + 1))
        else:
            raise ValueError('invalid shard expression')
    if not owned or len(owned) != len(set(owned)) or any(not 0 <= x < shard_count for x in owned):
        raise ValueError('invalid or duplicated shard ownership')
    return sorted(owned)


def make_identity(args):
    return dict(format=2, bound=str(args.bound), prime_cap=args.prime_cap,
                node_cap=str(args.node_cap), split_depth=args.split_depth,
                chunksize=args.chunksize, shards=args.shards)


def fresh_state(identity, owned):
    return {'generation': 0, 'identity': identity, 'owned': owned,
            'ranges': [], 'done_count': 0, 'counters': dict.fromkeys(COUNTERS, 0),
            'output_lines': [], 'open_records': [], 'limited': False}


def verify_state(state, identity, owned):
    if state.get('identity') != identity or state.get('owned') != owned:
        raise ValueError('checkpoint identity/ownership mismatch')
    ranges = CompletedRanges(state['ranges'])
    if ranges.count() != state['done_count'] or state['done_count'] < 0:
        raise ValueError('inconsistent checkpoint completion count')
    if any(type(state['counters'][key]) is not int or state['counters'][key] < 0 for key in COUNTERS):
        raise ValueError('invalid checkpoint counters')
    if not isinstance(state['output_lines'], list) or not isinstance(state['open_records'], list):
        raise ValueError('invalid checkpoint evidence')
    if sum(s.startswith('HIT ') for s in state['output_lines']) != state['counters']['hits']:
        raise ValueError('checkpoint HIT evidence/count mismatch')
    return ranges


def run(args):
    owned = parse_owned(args.owned, args.shards)
    owner_tag = ('s' + (str(owned[0]) + '-' + str(owned[-1])
                        if len(owned) > 1 and owned[-1] - owned[0] + 1 == len(owned)
                        else '-'.join(map(str, owned)))) if args.shards > 1 else ''
    log = RunLog(args.output_dir, owner_tag, make_identity(args), owned, args.device_id)
    try:
        return _run_search(args, owned, owner_tag, log)
    except (KeyboardInterrupt, InterruptedError):
        log.emit('INTERRUPTED', 'INTERRUPTED: resume from last durable checkpoint; uncheckpointed work may repeat')
        raise
    except Exception as error:
        log.emit('ERROR', f'ERROR: {error}', error_type=type(error).__name__, error=str(error))
        raise
    finally:
        log.close()


def _run_search(args, owned, owner_tag, log):
    owned_set = set(owned)
    identity = make_identity(args)
    checkpoint_dir = args.output_dir
    checkpoint_prefix = owner_tag + '_' if owner_tag else ''
    result_path = args.output_dir / ((owner_tag or 'result') + '.json')
    if args.fresh:
        checkpoint.clear(checkpoint_dir, checkpoint_prefix)
    state = checkpoint.load(checkpoint_dir, identity, checkpoint_prefix)
    if args.resume and state is None:
        raise ValueError('no valid matching checkpoint for --resume')
    if not args.resume and state is not None:
        raise ValueError('existing checkpoint: use --resume or --fresh')
    if state is None:
        state = fresh_state(identity, owned)
    ranges = verify_state(state, identity, owned)
    hit_lines = {line for line in state['output_lines'] if line.startswith('HIT ')}

    def report_hit(line, **fields):
        hit_lines.add(line)
        log.emit('HIT_FOUND', line, line=line, hits=len(hit_lines),
                 provisional=True, **fields)

    result_path.unlink(missing_ok=True)  # stale COMPLETE is never reused
    last_saved = time.monotonic()
    began = last_saved
    last_progress = began
    resume_generation = state['generation']
    log.emit('RESUME' if args.resume else 'START',
             f"{'RESUME' if args.resume else 'START'} scope={log.scope} device={log.device} owned={owned}/{args.shards} "
             f"workers={args.workers} depth={args.split_depth} checkpoint={args.checkpoint_seconds}s "
             f"prime_cap={args.prime_cap} ({args.prime_cap_mode}) "
             f"node_cap={'none' if args.node_cap_mode == 'unlimited' else args.node_cap}",
             identity=identity, generation=resume_generation, resume=args.resume,
             workers=args.workers, split_depth=args.split_depth, chunksize=args.chunksize,
             checkpoint_seconds=args.checkpoint_seconds, progress_seconds=args.progress_seconds,
             checkpoint_done=state['done_count'], checkpoint_counters=state['counters'],
             prime_cap=args.prime_cap, prime_cap_mode=args.prime_cap_mode,
             node_cap_mode=args.node_cap_mode, node_cap_effective=str(args.node_cap),
             assist_enabled=args.assist and args.node_cap_mode == 'unlimited',
             assist_poll=args.assist_poll, assist_mode='dfs-continuation')
    if args.assist and args.node_cap_mode == 'unlimited':
        # Reject stale Windows kernels that have no
        # continuation entrypoint and cannot execute this algorithm.
        probe = core.Search(args.bound, args.prime_cap, args.node_cap)
        if not hasattr(probe, 'visit_continuation') or not hasattr(probe, 'assist_check'):
            raise RuntimeError('native kernel lacks DFS continuation support: rebuild with build.ps1')
    path = base.prime_cache(args.prime_cap)
    base.attach_primes(path)
    parameters = (args.bound, args.prime_cap, args.node_cap)
    global_batch = local_batch = 0
    # pending connection -> (original batch ID, task count, start time).
    pending = {}
    # Only assisted batches need in-memory fragment aggregation. None of this
    # transient state is ever interpreted as a durable checkpoint.
    partial = {}
    fragment_queue = deque()
    fragment_connections = set()
    # A reservation consumes exactly one *idle* worker; one per donor.
    reservations = {}
    cooldown = {}
    next_request_token = 0
    assist_splits = 0
    assist_fragments = 0
    assist_nodes = 0
    last_assist_log = 0.0
    held_lines = []
    root_finished = False
    root = None

    def progress(now):
        nonlocal last_progress
        if now - last_progress < args.progress_seconds:
            return
        last_progress = now
        root_nodes = getattr(root, 'nodes', 0) if root is not None and 0 in owned_set else 0
        # Each returned fragment is either in partial or in a committed batch.
        partial_nodes = sum(batch['counts'][0] for batch in partial.values())
        nodes = state['counters']['nodes'] + root_nodes + partial_nodes
        hits = len(hit_lines)
        elapsed = round(now - began, 3)
        log.emit('PROGRESS',
                 f'PROGRESS nodes={nodes} hits={hits} batches_done={state["done_count"]} '
                 f'batches_created={local_batch} elapsed_seconds={elapsed}',
                 nodes=nodes, hits=hits, batches_done=state['done_count'],
                 batches_created=local_batch, global_batches_created=global_batch,
                 pending_batches=len({v[0] for v in pending.values()}),
                 active_fragments=len(pending),
                 idle_workers=len(pool.idle) + len(reservations),
                 assisted_batches=len(partial), assist_splits=assist_splits,
                 assist_fragments=assist_fragments, assist_nodes=assist_nodes,
                 elapsed_seconds=elapsed)

    def snapshot():
        nonlocal last_saved
        # Only called from the coordinator at a completed-result boundary.
        state['ranges'] = [r.copy() for r in ranges.ranges]
        state['generation'] += 1
        checkpoint.save(checkpoint_dir, state, checkpoint_prefix)
        last_saved = time.monotonic()
        log.emit('CHECKPOINT', f'CHECKPOINT generation={state["generation"]} batches_done={state["done_count"]}',
                 generation=state['generation'], batches_done=state['done_count'],
                 counters=state['counters'])

    def commit_batch(task_number, counts, limited, lines, unresolved):
        # Commit only whole original batches. Old A/B checkpoints and the
        # original CompletedRanges identity remain the sole recovery source.
        ranges.add(task_number)
        for name, value in zip(COUNTERS, counts):
            state['counters'][name] += int(value)
        state['done_count'] += 1
        state['output_lines'].extend(lines)
        state['open_records'].extend(str(value) for value in unresolved)
        state['limited'] |= bool(limited)
        for line in lines:
            if line.startswith(('OPEN_', 'UNRESOLVED ')):
                log.emit('UNRESOLVED', line, batch_id=task_number, line=line)

    def revoke_request(donor, delay=False):
        item = reservations.pop(donor, None)
        if item is None:
            return
        token, receiver, started = item
        pool.clear_request(donor)
        pool.idle.append(receiver)
        if delay:
            cooldown[donor] = time.monotonic() + args.assist_timeout

    def dispatch_fragments():
        # Ordinary streaming frontier jobs are always submitted first. This
        # queue is used only after the full ordinary frontier was dispatched.
        while fragment_queue:
            # Already-produced, real work outranks speculative assistance
            # requests. Reclaim reserved idle slots rather than holding them
            # for up to assist_timeout while ready fragments wait.
            if not pool.idle and reservations:
                revoke_request(next(iter(reservations)))
            if not pool.idle:
                break
            connection = pool.idle.popleft()
            batch_id, task = fragment_queue.popleft()
            pending[connection] = (batch_id, 1, time.monotonic())
            fragment_connections.add(connection)
            pool.submit([task], connection=connection)

    def maybe_assist():
        nonlocal next_request_token
        if not root_finished or not args.assist or args.node_cap_mode != 'unlimited':
            return
        dispatch_fragments()
        if fragment_queue:
            return
        now = time.monotonic()
        for donor, (_, _, started) in tuple(reservations.items()):
            if now - started >= args.assist_timeout:
                revoke_request(donor, delay=True)
        # Exactly one donor request per idle reservation, never broadcast.
        while pool.idle and pending:
            candidates = [(started, donor) for donor, (_, _, started) in pending.items()
                          if donor not in reservations and cooldown.get(donor, 0) <= now]
            if not candidates:
                break
            _, donor = min(candidates, key=lambda item: (item[0], pool.connections.index(item[1])))
            receiver = pool.idle.popleft()
            next_request_token += 1
            token = next_request_token
            reservations[donor] = (token, receiver, now)
            pool.set_request(donor, token)

    def periodic():
        now = time.monotonic()
        if now - last_saved >= args.checkpoint_seconds:
            snapshot()
        if now - last_progress >= args.progress_seconds:
            progress(now)
        maybe_assist()

    def add_partial(batch_id, counts, limited, lines, unresolved):
        # outstanding counts currently active + queued pieces. On split a
        # worker fragment becomes several new fragments. In all cases the
        # original batch commits exactly once when outstanding reaches zero.
        aggregation = partial[batch_id]
        for i, value in enumerate(counts):
            aggregation['counts'][i] += int(value)
        aggregation['limited'] |= bool(limited)
        aggregation['lines'].extend(lines)
        aggregation['unresolved'].extend(unresolved)
        aggregation['outstanding'] -= 1
        if aggregation['outstanding'] < 0:
            raise RuntimeError('negative outstanding fragment count')
        if aggregation['outstanding'] == 0:
            commit_batch(batch_id, aggregation['counts'], aggregation['limited'],
                         aggregation['lines'], aggregation['unresolved'])
            del partial[batch_id]
            log.emit('ASSIST_END', f'ASSIST_END batch={batch_id}', batch_id=batch_id,
                     total_assist_splits=assist_splits)

    def collect(timeout=None):
        nonlocal assist_splits, assist_fragments, assist_nodes, last_assist_log
        ready, _, _ = select.select(pool.connections, [], [], timeout)
        if not ready:
            periodic()
            return
        for connection in ready:
            try:
                message = connection.recv()
            except (EOFError, OSError) as error:
                raise RuntimeError('worker disconnected') from error
            if isinstance(message, BaseException):
                raise message
            if not isinstance(message, tuple) or len(message) not in (2, 3):
                raise RuntimeError('invalid worker message')
            tag, payload = message[:2]
            if tag == 'HIT':
                # Immediate, out-of-band hit notification from a still-running
                # job. It is diagnostic only: the batch result and its counters
                # stay authoritative, and this connection remains in `pending`.
                if len(message) != 2 or connection not in pending:
                    raise RuntimeError('unassigned HIT message')
                line = payload
                if (not isinstance(line, str) or not line.startswith('HIT ')
                        or '\n' in line or '\r' in line):
                    raise RuntimeError('invalid HIT message')
                report_hit(line, batch_id=pending[connection][0],
                           worker_pid=pool.connection_processes[connection].pid)
                continue
            if tag not in ('DONE', 'YIELD') or connection not in pending:
                raise RuntimeError('unexpected worker message')
            batch_id, expected_batch_size, _ = pending.pop(connection)
            was_fragment = connection in fragment_connections
            fragment_connections.discard(connection)
            revoke_request(connection)
            pool.idle.append(connection)
            if tag == 'YIELD':
                if len(message) != 3:
                    raise RuntimeError('invalid YIELD message')
                counts, limited, output, unresolved = payload
                # Every yielded task becomes a distinct, nonoverlapping
                # pending fragment. Never commit an unfinished original batch.
                remaining = message[2]  # Message length was validated above.
                if not isinstance(remaining, list) or len(remaining) < 1:
                    raise RuntimeError('YIELD has no remaining search')
                if len(counts) != len(COUNTERS) or limited:
                    raise RuntimeError('invalid yielded accounting')
                lines = output.splitlines()
                hit_lines.update(line for line in lines if line.startswith('HIT '))
                if sum(line.startswith('HIT ') for line in lines) != counts[1]:
                    raise RuntimeError('yielded HIT evidence/count mismatch')
                if batch_id not in partial:
                    partial[batch_id] = dict(outstanding=1, counts=[0]*len(COUNTERS),
                                             limited=False, lines=[], unresolved=[])
                aggregation = partial[batch_id]
                aggregation['outstanding'] += len(remaining)
                add_partial(batch_id, counts, limited, lines, unresolved)
                fragment_queue.extend((batch_id, task) for task in remaining)
                assist_splits += 1
                assist_fragments += len(remaining)
                if was_fragment:
                    assist_nodes += int(counts[0])
                cooldown[connection] = time.monotonic() + ASSIST_SPLIT_COOLDOWN
                now = time.monotonic()
                if assist_splits == 1 or now - last_assist_log >= 15.0:
                    log.emit('ASSIST_SPLIT',
                             f'ASSIST_SPLIT batch={batch_id} splits={assist_splits} pieces={len(remaining)}',
                             batch_id=batch_id, splits=assist_splits,
                             pieces=len(remaining), queued_fragments=len(fragment_queue),
                             donor_pid=pool.connection_processes[connection].pid)
                    last_assist_log = now
            else:
                if len(message) != 2:
                    raise RuntimeError('invalid DONE message')
                counts, limited, output, batch_done, unresolved = payload
                if batch_done != expected_batch_size or len(counts) != len(COUNTERS):
                    raise RuntimeError('invalid worker result shape')
                lines = output.splitlines()
                hit_lines.update(line for line in lines if line.startswith('HIT '))
                if sum(line.startswith('HIT ') for line in lines) != counts[1]:
                    raise RuntimeError('worker HIT evidence/count mismatch')
                if was_fragment:
                    assist_nodes += int(counts[0])
                if batch_id in partial:
                    add_partial(batch_id, counts, limited, lines, unresolved)
                else:
                    commit_batch(batch_id, counts, limited, lines, unresolved)
        periodic()

    def submit(batch):
        nonlocal global_batch, local_batch
        batch_id = global_batch
        global_batch += 1
        if batch_id % args.shards not in owned_set:
            return
        item_id = local_batch
        local_batch += 1
        if ranges.contains(item_id):
            return
        while not pool.idle:
            collect(max(0.0, min(1.0, args.checkpoint_seconds - (time.monotonic() - last_saved))))
        connection = pool.idle[0]
        pending[connection] = (item_id, len(batch), time.monotonic())
        pool.submit(batch)
        periodic()

    context = mp.get_context(args.start_method)
    with base.WorkerPool(context, args.workers, path, parameters,
                         assist_poll=args.assist_poll,
                         assist_enabled=args.assist and args.node_cap_mode == 'unlimited',
                         notify_hits=True) as pool:
        root = base.SplitSearch(*parameters, args.split_depth, args.chunksize, submit)
        # Depth-four generation is STREAMING; no all_batches list. The frontier
        # can itself find a hit while splitting; report it immediately instead
        # of holding it in held_lines until the run ends.
        def frontier_hit(line):
            if 0 in owned_set:
                report_hit(line, origin='frontier')

        root_output = base._HitTee(frontier_hit)
        with contextlib.redirect_stdout(root_output):
            root.visit(1, 1, 1, ())
        held_lines = root_output.getvalue().splitlines()
        if root.batch and not root.stopped:
            batch, root.batch = root.batch, []
            submit(batch)
        root_finished = not root.stopped
        maybe_assist()
        while pending or fragment_queue:
            collect(max(0.0, min(1.0, args.checkpoint_seconds - (time.monotonic() - last_saved))))

    if partial or reservations or pending or fragment_queue or fragment_connections:
        raise RuntimeError('incomplete continuation task accounting at finish')
    if root_finished and ranges.count() != local_batch:
        raise RuntimeError('missing completed search batches')
    if ranges.ranges and ranges.ranges[-1][1] >= local_batch:
        raise RuntimeError('checkpoint references batches outside generated frontier')
    root_counts = {name: getattr(root, name) if 0 in owned_set else 0 for name in COUNTERS}
    all_counts = {name: state['counters'][name] + root_counts[name] for name in COUNTERS}
    all_lines = list(state['output_lines']) + (held_lines if 0 in owned_set else [])
    if sum(line.startswith('HIT ') for line in all_lines) != all_counts['hits']:
        raise RuntimeError('root HIT evidence/count mismatch')
    status = ('COMPLETE' if root_finished and not state['limited']
              and local_batch == state['done_count']
              and not any(all_counts[k] for k in COUNTERS[2:])
              and all_counts['nodes'] <= args.node_cap else 'INCOMPLETE')
    # A durable generation is the only trusted resume point.
    snapshot()
    report = {'status': status, 'identity': identity, 'owned': owned,
              'batches_total': global_batch, 'local_batches': local_batch,
              'counters': all_counts, 'output_lines': all_lines,
              'open_records': state['open_records'] + ([str(x) for x in root.unresolved] if 0 in owned_set else []),
              'limited': bool(state['limited'] or not root_finished or all_counts['nodes'] > args.node_cap),
              'elapsed_seconds': round(time.monotonic() - began, 3),
              'backend': str(Path(core.__file__).name),
              'assist': {'splits': assist_splits, 'fragments': assist_fragments,
                         'helper_nodes': assist_nodes}}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    temporary = result_path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, result_path)
    log.emit('FINISH', f"{status} nodes={all_counts['nodes']} hits={all_counts['hits']} "
             f"open_factor={all_counts['open_factor']} open_branch={all_counts['open_branch']} "
             f"open_candidates={all_counts['open_candidates']} completed_batches={local_batch} "
             f'all_batches={global_batch} elapsed={report["elapsed_seconds"]}s',
             status=status, counters=all_counts, completed_batches=local_batch,
             all_batches=global_batch, elapsed_seconds=report['elapsed_seconds'],
             assist_splits=assist_splits, assist_fragments=assist_fragments,
             assist_nodes=assist_nodes,
             result_file=str(result_path))
    return 0 if status == 'COMPLETE' else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bound', type=int, default=core.DEFAULT_BOUND)
    parser.add_argument('--prime-cap', default='auto', help='auto (default) or integer; manual cap may leave open branches')
    parser.add_argument('--node-cap', default='none', help='none (default) or positive integer for test limits')
    parser.add_argument('--workers', type=int, default=base.default_workers())
    parser.add_argument('--split-depth', type=int, default=4)
    parser.add_argument('--chunksize', type=int, default=16)
    parser.add_argument('--no-assist', action='store_true',
                        help='disable dynamic load balancing through DFS continuation splitting')
    parser.add_argument('--assist-poll', type=int, default=1024,
                        help='candidate or child boundaries between yield polls (default 1024)')
    parser.add_argument('--assist-timeout', type=float, default=30.0,
                        help='seconds before reassigning an unanswered work-splitting request')
    parser.add_argument('--start-method', choices=mp.get_all_start_methods(), default='spawn' if os.name == 'nt' else 'forkserver')
    parser.add_argument('--shards', type=int, default=1)
    parser.add_argument('--owned', default='', help='e.g. 0:1 or 0,2; empty means all')
    parser.add_argument('--checkpoint-seconds', type=int, default=900)
    parser.add_argument('--output-dir', type=Path, help='directory for logs, results and checkpoints')
    parser.add_argument('--device-id', default='', help='optional device label (default: hostname)')
    parser.add_argument('--progress-seconds', type=float, default=30.0, help='console/file progress interval')
    exclusive = parser.add_mutually_exclusive_group()
    exclusive.add_argument('--resume', action='store_true')
    exclusive.add_argument('--fresh', action='store_true')
    args = parser.parse_args()
    args.assist = not args.no_assist
    if args.bound < 2:
        parser.error('bound must be at least 2')
    if min(args.workers, args.split_depth, args.chunksize, args.shards, args.checkpoint_seconds) < 1:
        parser.error('workers, split-depth, chunksize, shards and checkpoint-seconds must be positive')
    if args.progress_seconds <= 0 or not 1 <= args.assist_poll <= 0xffffffff or args.assist_timeout <= 0:
        parser.error('invalid progress/assist parameters')
    if args.output_dir is None:
        digits = str(args.bound)
        label = '1e' + str(len(digits) - 1) if digits[0] == '1' and set(digits[1:]) == {'0'} else digits
        args.output_dir = Path('log') / label
    try:
        resolve_caps(args)
    except ValueError as error:
        parser.error(str(error))
    if os.name == 'nt' and args.workers > 512:
        parser.error('Windows supports at most 512 workers')
    try:
        return run(args)
    except (KeyboardInterrupt, InterruptedError):
        print('INTERRUPTED: no partially committed state saved; resume from last checkpoint', flush=True)
        return 130
    except (OSError, ValueError, RuntimeError) as error:
        print(f'ERROR: {error}', flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
