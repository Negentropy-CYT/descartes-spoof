"""Best-effort, append-only operational logging. Never a recovery source.

The logging file is not validated or read when a search starts/resumes: even a
truncated/malformed previous record is irrelevant to search correctness.
"""
from __future__ import annotations
import json
from pathlib import Path
import socket
import sys
from datetime import datetime, timezone
from uuid import uuid4


class RunLog:
    def __init__(self, directory, owner_tag, identity, owned, device_id=''):
        self.session = uuid4().hex
        self.scope = 'FULL' if identity['shards'] == 1 else 'SHARDED'
        try:
            self.device = device_id or socket.gethostname()
        except OSError:
            self.device = device_id or 'unknown-device'
        self.metadata = {
            'scope': self.scope, 'shard_count': identity['shards'],
            'owned_shards': owned, 'device': self.device,
            'owner_tag': owner_tag, 'session_id': self.session,
        }
        self.path = Path(directory) / ((owner_tag or 'run') + '.log')
        self.console = sys.stdout  # stable during redirect_stdout in frontier generation
        self._file = None
        self._warned = False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Append: never truncate or read existing bytes, including broken log lines.
            self._file = self.path.open('a', encoding='utf-8', buffering=1)
            # Start every process session at a new record boundary even when
            # an interrupted previous write left a truncated final line.
            self._file.write('\n')
            self._file.flush()
        except Exception as err:
            self._file_failed(err)

    def _console(self, message):
        try:
            print(message, file=self.console, flush=True)
        except Exception:
            pass  # Console/log failure is not a reason to interrupt the search.

    def _file_failed(self, error):
        if self._file is not None:
            try:
                self._file.close()
            except Exception:
                pass
        self._file = None
        if not self._warned:
            self._warned = True
            self._console(f'LOG_WARNING file logging unavailable ({type(error).__name__}); computation continues')

    def emit(self, event, message, **fields):
        """A complete single-line event. Logging failures cannot abort computation."""
        self._console(message)
        if self._file is None:
            return
        try:
            record = {'time_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                      'event': event, **self.metadata, **fields}
            self._file.write(json.dumps(record, ensure_ascii=False, separators=(',', ':'), default=str) + '\n')
            self._file.flush()  # No fsync: logs do not carry durable search state.
        except Exception as error:
            self._file_failed(error)

    def close(self):
        if self._file is not None:
            try:
                self._file.close()
            except Exception:
                pass
            self._file = None
