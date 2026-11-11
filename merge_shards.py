"""Validate and aggregate offline search results without rerunning the search."""
import argparse
import json
import os
from pathlib import Path
import tempfile

COUNTERS = ('nodes', 'hits', 'open_factor', 'open_branch', 'open_candidates')


def numbered_ranges(values):
    """Compact sorted internal IDs into zero-based ranges."""
    spans = []
    for value in sorted(values):
        if spans and value == spans[-1][1] + 1:
            spans[-1][1] = value
        else:
            spans.append([value, value])
    return format_ranges(spans)


def format_ranges(spans):
    return ', '.join(str(a) if a == b else f'{a}-{b}' for a, b in spans)


def _integer(value, label, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError('{v0} must be an integer >= {v1}'.format(v0=label, v1=minimum))
    return value


def _decimal(value, label, minimum):
    if not isinstance(value, str) or not value.isascii() or not value.isdigit():
        raise ValueError('Invalid {v0} format'.format(v0=label))
    return _integer(int(value), label, minimum)


def _validate(record):
    if not isinstance(record, dict):
        raise ValueError('Result must be a JSON object')
    identity = record['identity']
    if not isinstance(identity, dict) or type(identity.get('format')) is not int or identity['format'] != 2:
        raise ValueError('Unsupported result format')
    _decimal(identity['bound'], 'Search bound', 2)
    cap = _decimal(identity['node_cap'], 'Node cap', 1)
    for key in ('prime_cap', 'split_depth', 'chunksize', 'shards'):
        _integer(identity[key], key, 3 if key == 'prime_cap' else 1)
    count = identity['shards']
    total = _integer(record['batches_total'], 'Global batch count')
    owned = record['owned']
    if not isinstance(owned, list) or not owned:
        raise ValueError('Missing ownership; select original run results')
    if any(type(n) is not int or not 0 <= n < count for n in owned) or len(set(owned)) != len(owned):
        raise ValueError('Owned IDs are out of range or duplicated')
    expected = sum(total // count + (n < total % count) for n in owned)
    if _integer(record['local_batches'], 'Local batch count') != expected:
        raise ValueError('Local batch count mismatch; expected {v0}'.format(v0=expected))
    if record['status'] != 'COMPLETE':
        raise ValueError('Search is not complete')
    if record['limited'] is not False or record['open_records'] != []:
        raise ValueError('Limits or unresolved records remain')
    counters = record['counters']
    if not isinstance(counters, dict):
        raise ValueError('Invalid counters')
    for name in COUNTERS:
        _integer(counters[name], name)
    if any(counters[name] for name in COUNTERS[2:]):
        raise ValueError('Unresolved work remains')
    if counters['nodes'] > cap:
        raise ValueError('Node count exceeds cap')
    lines = record['output_lines']
    if not isinstance(lines, list) or any(not isinstance(line, str) or '\n' in line or '\r' in line for line in lines):
        raise ValueError('Invalid result lines')
    if sum(line.startswith('HIT ') for line in lines) != counters['hits']:
        raise ValueError('HIT evidence/count mismatch')


def inspect_results(paths):
    """Return display entries, errors and a merge only after complete validation."""
    entries, errors, reports = [], [], []
    for path in map(Path, paths):
        entry = {'path': str(path), 'owned': '—', 'status': 'Invalid'}
        entries.append(entry)
        try:
            record = json.loads(path.read_text(encoding='utf-8-sig'))
            _validate(record)
        except (OSError, UnicodeError, ValueError, KeyError, TypeError, OverflowError) as exc:
            message = 'Missing field {v0}'.format(v0=exc.args[0]) if isinstance(exc, KeyError) else str(exc)
            entry['error'] = message
            errors.append(f'{path}: {message}')
            continue
        entry['owned'] = numbered_ranges(record['owned'])
        entry['status'] = 'Complete'
        reports.append((record, entry))
    identity = reports[0][0]['identity'] if reports else None
    total = reports[0][0]['batches_total'] if reports else None
    covered, counters, lines = set(), dict.fromkeys(COUNTERS, 0), []
    for record, entry in reports:
        differences = sorted(key for key in set(identity) | set(record['identity'])
                             if identity.get(key) != record['identity'].get(key))
        issue = None
        if differences:
            issue = 'Parameter mismatch: ' + ', '.join(differences)
        elif record['batches_total'] != total:
            issue = 'Global batch counts differ'
        elif covered.intersection(record['owned']):
            issue = 'Duplicate IDs: ' + numbered_ranges(covered.intersection(record['owned']))
        if issue:
            entry['status'] = 'Conflict'
            errors.append(f'{entry["path"]}: {issue}')
            continue
        covered.update(record['owned'])
        for name in COUNTERS:
            counters[name] += record['counters'][name]
        lines.extend(record['output_lines'])
    if identity:
        gaps, start = [], 0
        for n in sorted(covered):
            if start < n:
                gaps.append((start, n - 1))
            start = n + 1
        if start < identity['shards']:
            gaps.append((start, identity['shards'] - 1))
        if gaps:
            errors.append('Missing IDs: ' + format_ranges(gaps))
        if counters['nodes'] > int(identity['node_cap']):
            errors.append('Merged node count exceeds global cap')
    elif not entries:
        errors.append('Add result files')
    merged = None if errors else dict(status='COMPLETE', identity=identity, batches_total=total,
                                      shards_covered=sorted(covered), counters=counters, output_lines=lines)
    return dict(entries=entries, errors=errors, identity=identity, covered=len(covered), merged=merged)


def merge(paths):
    result = inspect_results(paths)
    if result['errors']:
        raise ValueError('\n'.join(result['errors']))
    return result['merged']


def save_merged(paths, output):
    """Re-read inputs and replace output atomically; never overwrite an input."""
    paths, output = list(map(Path, paths)), Path(output)
    for path in paths:
        if output.resolve() == path.resolve() or (output.exists() and path.exists() and os.path.samefile(output, path)):
            raise ValueError('Output must not overwrite an input result')
    merged = merge(paths)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=output.parent,
                                         prefix='.merge-', suffix='.tmp', delete=False) as file:
            temporary = Path(file.name)
            json.dump(merged, file, indent=2, ensure_ascii=False)
            file.write('\n')
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return merged


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', nargs='+', type=Path)
    parser.add_argument('--output', type=Path, default=Path('merged.json'))
    args = parser.parse_args()
    try:
        merged = save_merged(args.results, args.output)
    except (OSError, ValueError) as exc:
        parser.exit(2, f'REJECTED: {exc}\n')
    print(f"COMPLETE merged nodes={merged['counters']['nodes']} hits={merged['counters']['hits']}")


if __name__ == '__main__':
    main()
