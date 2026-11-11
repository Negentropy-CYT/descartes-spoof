"""Small, independent A/B checkpoint storage. No scheduler dependencies."""
from __future__ import annotations
import json
import os
from pathlib import Path
import struct
import zlib

MAGIC = b'SPOOFCRC1'
HEADER = struct.Struct('!9sQI')
MAX_BYTES = 64 * 1024 * 1024


def _encode(state):
    payload = zlib.compress(json.dumps(state, sort_keys=True, separators=(',', ':')).encode('utf-8'), 6)
    if len(payload) > MAX_BYTES:
        raise ValueError('checkpoint exceeds 64 MiB; refuse unbounded checkpoint')
    return HEADER.pack(MAGIC, len(payload), zlib.crc32(payload) & 0xffffffff) + payload


def _decode(data):
    if len(data) < HEADER.size:
        raise ValueError('truncated checkpoint')
    magic, length, checksum = HEADER.unpack_from(data)
    if magic != MAGIC or length > MAX_BYTES or len(data) != HEADER.size + length:
        raise ValueError('invalid checkpoint header or length')
    payload = data[HEADER.size:]
    if zlib.crc32(payload) & 0xffffffff != checksum:
        raise ValueError('checkpoint CRC32 mismatch')
    value = json.loads(zlib.decompress(payload))
    if not isinstance(value, dict) or not isinstance(value.get('generation'), int):
        raise ValueError('malformed checkpoint state')
    return value


def load(directory, identity, prefix=''):
    """Return newest valid matching checkpoint, or None. Fail closed on mismatch."""
    directory = Path(directory)
    matches, mismatches, invalid = [], [], []
    for letter in 'AB':
        path = directory / (prefix + letter + '.ckpt')
        if not path.exists():
            continue
        try:
            state = _decode(path.read_bytes())
            if state.get('identity') == identity:
                matches.append(state)
            else:
                mismatches.append(path.name)
        except (OSError, ValueError, zlib.error, UnicodeDecodeError, json.JSONDecodeError):
            invalid.append(path.name)
            continue
    if matches:
        return max(matches, key=lambda s: s['generation'])
    if mismatches:
        raise ValueError('existing checkpoint is for different search parameters: ' + ', '.join(mismatches))
    if invalid:
        raise ValueError('no readable checkpoint: incompatible format or damaged files: ' + ', '.join(invalid))
    return None


def save(directory, state, prefix=''):
    """Overwrite only one slot; keep prior generation in the other slot."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    letter = 'A' if state['generation'] % 2 == 0 else 'B'
    target = directory / (prefix + letter + '.ckpt')
    temporary = directory / (target.name + '.tmp')
    with temporary.open('wb') as file:
        file.write(_encode(state))
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, target)
    if os.name == 'posix':
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return target


def clear(directory, prefix=''):
    directory = Path(directory)
    for suffix in ('A', 'B'):
        (directory / (prefix + suffix + '.ckpt')).unlink(missing_ok=True)
