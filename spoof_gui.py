"""Small standalone desktop launcher for the existing Spoof search program.

Launches the search engine and displays its JSON-lines logs.
Search and checkpoints run independently; result merging uses the offline validator.
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk


APP_DIR = Path(sys.executable if getattr(sys, 'frozen', False) else __file__).resolve().parent
POLL_MS = 1000
PREFS_FILE = APP_DIR / 'desktop_settings.json'
VIEW_LINES = 800
READ_LIMIT = 128 * 1024


def parse_bound(raw: str) -> int:
    """Accept decimal integers and exact powers of ten (1e70)."""
    value = raw.strip().replace('_', '')
    match = re.fullmatch(r'1[eE](\d{1,3})', value)
    if match:
        exponent = int(match.group(1))
        if exponent < 1:
            raise ValueError('Search bound must exceed 1')
        if exponent > 300:
            raise ValueError('Exponent must not exceed 300')
        return 10 ** exponent
    if not re.fullmatch(r'[0-9]+', value):
        raise ValueError('Enter a positive integer or a bound such as 1e70')
    if len(value) > 301:
        raise ValueError('Search bound must not exceed 301 digits')
    bound = int(value)
    if bound < 2:
        raise ValueError('Search bound must exceed 1')
    return bound


def bound_label(bound: int) -> str:
    value = str(bound)
    if len(value) >= 2 and value[0] == '1' and set(value[1:]) == {'0'}:
        return '1e' + str(len(value) - 1)
    return value


def owned_ids(text: str, count: int) -> list[int]:
    if count < 1:
        raise ValueError('Total shards must exceed 0')
    if not text.strip():
        return list(range(count))
    values = []
    for item in text.split(','):
        part = item.strip()
        if re.fullmatch(r'\d+', part):
            values.append(int(part))
        elif re.fullmatch(r'\d+:\d+', part):
            a, b = map(int, part.split(':'))
            if b < a:
                raise ValueError('Shard range start must not exceed its end')
            values.extend(range(a, b + 1))
        else:
            raise ValueError('Shard syntax: 0:3 or 0,2,4')
    if not values or len(set(values)) != len(values) or min(values) < 0 or max(values) >= count:
        raise ValueError('Shard IDs are out of range or duplicated')
    return sorted(values)


def owner_tag(count: int, owned: list[int]) -> str:
    if count == 1:
        return ''
    if len(owned) > 1 and owned == list(range(owned[0], owned[-1] + 1)):
        return 's' + str(owned[0]) + '-' + str(owned[-1])
    return 's' + '-'.join(map(str, owned))


def shard_selection(text: str, count: int) -> str:
    if not text.strip():
        raise ValueError('Enter assigned shard IDs, e.g. 0-1 or 0,2.')
    selected = owned_ids(text.replace('，', ',').replace('-', ':'), count)
    return ','.join(map(str, selected))


def make_config(bound_text, workers_text, is_sharded, shard_count_text, owned_text,
                depth_text, chunksize_text, device, output_override='') -> dict:
    bound = parse_bound(bound_text)
    try:
        workers = int(workers_text)
        shards = int(shard_count_text) if is_sharded else 1
        depth = int(depth_text)
        chunksize = int(chunksize_text)
    except ValueError as exc:
        raise ValueError('Workers, shards, depth and batch size must be integers') from exc
    if workers < 1 or depth < 1 or chunksize < 1 or shards < 1 or workers > 512:
        raise ValueError('Workers, depth, batch size and shards must be positive (workers at most 512)')
    if is_sharded and shards == 1:
        raise ValueError('Shard mode requires more than one shard')
    selected = owned_ids(owned_text, shards) if is_sharded else [0]
    tag = owner_tag(shards, selected)
    out = Path(output_override.strip()) if output_override.strip() else Path('log') / bound_label(bound)
    if not out.is_absolute():
        out = APP_DIR / out
    return dict(bound=bound, workers=workers, depth=depth, chunksize=chunksize,
                shards=shards, owned=selected, tag=tag, owned_text=owned_text.strip() if is_sharded else '',
                device=device.strip(), output_dir=out.resolve())


def launch_args(cfg: dict, action: str, engine_path: Path | None = None) -> list[str]:
    """Create argv without shell interpolation; backend owns all search semantics."""
    if action not in ('fresh', 'resume'):
        raise ValueError('Invalid action')
    if engine_path is not None:
        command = [str(engine_path)]
    elif os.name == 'nt':
        python = APP_DIR / '.venv' / 'Scripts' / 'python.exe'
        if not python.is_file():
            raise FileNotFoundError('Missing .venv\\Scripts\\python.exe; use the portable app or configure Python')
        command = [str(python), '-B', '-u', str(APP_DIR / 'runner.py')]
    else:
        command = [sys.executable, '-B', '-u', str(APP_DIR / 'runner.py')]
    argv = command + [
        '--bound', str(cfg['bound']), '--prime-cap', 'auto', '--node-cap', 'none',
        '--workers', str(cfg['workers']), '--split-depth', str(cfg['depth']),
        '--chunksize', str(cfg['chunksize']), '--shards', str(cfg['shards']),
        '--checkpoint-seconds', '900', '--progress-seconds', '30',
        '--start-method', 'spawn' if os.name == 'nt' else 'forkserver',
        '--output-dir', str(cfg['output_dir']),
    ]
    if cfg['owned_text']:
        argv += ['--owned', cfg['owned_text']]
    if cfg['device']:
        argv += ['--device-id', cfg['device']]
    argv.append('--' + action)
    return argv


def process_birth_token(pid: int) -> int | None:
    """Guard against stopping a reused PID when reattaching on Windows."""
    if os.name != 'nt':
        try:
            text = Path(f'/proc/{pid}/stat').read_text(encoding='ascii')
            return int(text.rsplit(') ', 1)[1].split()[19])
        except (OSError, ValueError, IndexError):
            return None
    try:
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = (wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                           ctypes.c_void_p, ctypes.c_void_p)
        kernel.GetProcessTimes.restype = wintypes.BOOL
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x1000 | 0x100000, False, pid)
        if not handle:
            return None
        try:
            if kernel.WaitForSingleObject(handle, 0) != 258:  # WAIT_TIMEOUT: still running
                return None
            stamps = [wintypes.FILETIME() for _ in range(4)]
            if not kernel.GetProcessTimes(handle, *map(ctypes.byref, stamps)):
                return None
            return (stamps[0].dwHighDateTime << 32) | stamps[0].dwLowDateTime
        finally:
            kernel.CloseHandle(handle)
    except (OSError, AttributeError, ValueError):
        return None


def active_pid(record: dict | None) -> bool:
    if not isinstance(record, dict) or type(record.get('pid')) is not int:
        return False
    current = process_birth_token(record['pid'])
    return current is not None and current == record.get('birth_token')


def log_filename(cfg: dict) -> Path:
    return cfg['output_dir'] / ((cfg['tag'] or 'run') + '.log')


def marker_filename(cfg: dict) -> Path:
    return cfg['output_dir'] / ('.gui_' + (cfg['tag'] or 'run') + '.json')


def friendly_event(raw_line: bytes) -> tuple[str | None, dict | None]:
    try:
        record = json.loads(raw_line.decode('utf-8'))
        if not isinstance(record, dict):
            return None, None
        event = str(record.get('event', 'EVENT'))
        stamp = str(record.get('time_utc', ''))[:19].replace('T', ' ')
        prefix = f'[{stamp}] {event}'
        if event == 'PROGRESS':
            message = (f"nodes={record.get('nodes', '?')} hits={record.get('hits', '?')} "
                       f"batches={record.get('batches_done', '?')}/{record.get('batches_created', '?')} "
                       f"elapsed={record.get('elapsed_seconds', '?')}s")
        elif event == 'FINISH':
            counts = record.get('counters', {})
            message = f"{record.get('status', '?')} nodes={counts.get('nodes', '?')} hits={counts.get('hits', '?')}"
        elif event == 'CHECKPOINT':
            message = f"generation={record.get('generation', '?')} batches_done={record.get('batches_done', '?')}"
        elif event in ('START', 'RESUME'):
            message = (f"scope={record.get('scope', '?')} device={record.get('device', '?')} "
                       f"shards={record.get('owned_shards', [])}/{record.get('shard_count', '?')} "
                       f"workers={record.get('workers', '?')}")
        else:
            message = str(record.get('line') or record.get('error') or '')
        return prefix + ('  ' + message if message else ''), record
    except (UnicodeError, ValueError, TypeError):
        return None, None


class LogTail:
    """Read only new bytes; damaged records are ignored, never affect engine."""
    def __init__(self):
        self.path = None
        self.offset = 0
        self.pending = b''
        self.warning_shown = False

    def select(self, path: Path, recent=True, offset=None):
        self.path = path
        self.offset = 0
        self.pending = b''
        self.warning_shown = False
        try:
            size = path.stat().st_size
            if offset is not None:
                self.offset = min(max(0, offset), size)
                return
            if recent and size > READ_LIMIT:
                self.offset = size - READ_LIMIT
                with path.open('rb') as file:
                    file.seek(self.offset)
                    file.readline()  # drop one partial line
                    self.offset = file.tell()
        except OSError:
            pass

    def poll(self):
        if self.path is None:
            return []
        try:
            size = self.path.stat().st_size
            if size < self.offset:
                self.offset, self.pending = 0, b''
            with self.path.open('rb') as file:
                file.seek(self.offset)
                chunk = file.read(READ_LIMIT)
                self.offset = file.tell()
        except OSError:
            return []
        contents = self.pending + chunk
        lines = contents.split(b'\n')
        self.pending = lines.pop()[-READ_LIMIT:]
        out = []
        for line in lines:
            if not line.strip():
                continue
            message, record = friendly_event(line)
            if message is not None:
                out.append((message, record))
            elif not self.warning_shown:
                out.append(('[Log warning] Invalid record skipped; search and recovery are unaffected.', None))
                self.warning_shown = True
        return out


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Spoof Search')
        icon = Path(__file__).resolve().parent / 'assets' / 'icon.ico'
        if icon.is_file():
            self.iconbitmap(default=str(icon))
        self.geometry('1160x760')
        self.minsize(940, 600)
        self.proc = None
        self.record = None
        self.config_now = None
        self.tail = LogTail()
        self._setup()
        self._load_preferences()
        self.after(150, self._restore_view)
        self.protocol('WM_DELETE_WINDOW', self._close)
        self.after(POLL_MS, self._tick)

    def _setup(self):
        self.bound = tk.StringVar(value='1e35')
        self.workers = tk.StringVar(value=str(min(os.cpu_count() or 12, 12)))
        self.mode = tk.StringVar(value='full')
        self.shards = tk.StringVar(value='4')
        self.owned = tk.StringVar(value='0:1')
        self.owned_display = tk.StringVar(value='0-1')
        self.shard_hint = tk.StringVar()
        self.depth = tk.StringVar(value='4')
        self.chunksize = tk.StringVar(value='16')
        self.device = tk.StringVar(value=socket.gethostname())
        self.directory = tk.StringVar(value='')
        self.status = tk.StringVar(value='Not connected')
        self.metrics = {key: tk.StringVar(value='—') for key in ('nodes', 'hits', 'batches')}
        self.log_source = tk.StringVar(value='No run selected')

        self.configure(background='#eef2f7')
        self.option_add('*Font', ('Segoe UI', 10))
        style = ttk.Style(self)
        if 'vista' in style.theme_names():
            style.theme_use('vista')
        style.configure('.', font=('Segoe UI', 10), foreground='#23324a')
        style.configure('TFrame', background='#ffffff')
        style.configure('TLabel', background='#ffffff', foreground='#23324a')
        style.configure('Muted.TLabel', foreground='#708096')
        style.configure('Title.TLabel', font=('Segoe UI', 20, 'bold'))
        style.configure('Section.TLabel', font=('Segoe UI', 11, 'bold'))
        style.configure('Value.TLabel', font=('Segoe UI', 23, 'bold'), foreground='#162b49')
        style.configure('TEntry', padding=6, fieldbackground='#f8fafc', bordercolor='#dce3ed')
        style.configure('TButton', padding=(10, 6))
        style.configure('Primary.TButton', font=('Segoe UI', 10, 'bold'))
        style.configure('TRadiobutton', background='white', padding=(0, 3))
        style.configure('TCheckbutton', background='white', padding=(0, 4))

        outer = tk.Frame(self, background='#eef2f7')
        outer.pack(fill='both', expand=True, padx=20, pady=18)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(1, weight=1)
        header = tk.Frame(outer, background='#eef2f7')
        header.grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 16))
        tk.Label(header, text='Spoof Search', font=('Segoe UI', 23, 'bold'), bg='#eef2f7', fg='#14243b').pack(side='left')
        panel = ttk.Frame(outer, width=320, padding=16)
        panel.grid(row=1, column=0, sticky='nsew', padx=(0, 16))
        panel.grid_propagate(False)
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(1, weight=1)
        ttk.Label(panel, text='Search settings', style='Section.TLabel').grid(row=0, column=0, sticky='w', pady=(0, 12))
        viewport = ttk.Frame(panel)
        viewport.grid(row=1, column=0, sticky='nsew')
        viewport.columnconfigure(0, weight=1)
        viewport.rowconfigure(0, weight=1)
        self.settings_canvas = tk.Canvas(viewport, background='white', highlightthickness=0, width=260)
        self.settings_canvas.grid(row=0, column=0, sticky='nsew')
        scrollbar = tk.Scrollbar(viewport, orient='vertical', command=self.settings_canvas.yview)
        scrollbar.grid(row=0, column=1, sticky='ns', padx=(5, 0))
        self.settings_canvas.configure(yscrollcommand=scrollbar.set)
        self.settings = ttk.Frame(self.settings_canvas, padding=(0, 0, 8, 12))
        item = self.settings_canvas.create_window((0, 0), window=self.settings, anchor='nw')
        self.settings.bind('<Configure>', lambda event: self.settings_canvas.configure(scrollregion=self.settings_canvas.bbox('all')))
        self.settings_canvas.bind('<Configure>', lambda event: self.settings_canvas.itemconfigure(item, width=event.width))
        self.bind('<MouseWheel>', self._scroll_settings, add='+')
        self._field(self.settings, 'Search bound', self.bound)
        self._field(self.settings, 'Workers', self.workers)
        ttk.Label(self.settings, text='Search mode', style='Section.TLabel').pack(anchor='w', pady=(16, 4))
        ttk.Radiobutton(self.settings, text='Full search', variable=self.mode, value='full', command=self._mode_changed).pack(anchor='w')
        ttk.Radiobutton(self.settings, text='Offline shards', variable=self.mode, value='shard', command=self._mode_changed).pack(anchor='w')
        self.shard_frame = ttk.Frame(self.settings)
        self._field(self.shard_frame, 'Total shards', self.shards)
        self._field(self.shard_frame, 'Assigned shard IDs', self.owned_display)
        ttk.Label(self.shard_frame, text='IDs start at 0, e.g. 0-1 or 0,2', style='Muted.TLabel', wraplength=250).pack(anchor='w', pady=(5, 0))
        ttk.Label(self.shard_frame, textvariable=self.shard_hint, style='Muted.TLabel', wraplength=250).pack(anchor='w', pady=(4, 0))
        self.shards.trace_add('write', self._shard_summary)
        self.owned_display.trace_add('write', self._shard_summary)
        self._shard_summary()
        self.show_advanced = tk.BooleanVar(value=False)
        self.advanced_toggle = ttk.Checkbutton(self.settings, text='Advanced settings', variable=self.show_advanced, command=self._toggle_advanced)
        self.advanced_toggle.pack(anchor='w', pady=(12, 0))
        self.advanced_frame = ttk.Frame(self.settings)
        self._field(self.advanced_frame, 'Split depth', self.depth)
        self._field(self.advanced_frame, 'Subtrees per batch', self.chunksize)
        self._field(self.advanced_frame, 'Device label', self.device)
        self.output_section = ttk.Frame(self.settings)
        self.output_section.pack(fill='x', pady=(12, 0))
        self._field(self.output_section, 'Output directory', self.directory)
        ttk.Label(self.output_section, text='Leave blank for the default directory', style='Muted.TLabel', wraplength=250).pack(anchor='w', pady=(3, 5))
        ttk.Button(self.output_section, text='Browse…', command=self._choose_folder).pack(anchor='e')
        self._mode_changed()

        actions = ttk.Frame(panel)
        actions.grid(row=2, column=0, sticky='ew', pady=(12, 0))
        actions.columnconfigure((0, 1), weight=1)
        ttk.Separator(actions).grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 12))
        self.start_button = ttk.Button(actions, text='Start new search', style='Primary.TButton', command=lambda: self._start('fresh'))
        self.start_button.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(0, 7))
        ttk.Button(actions, text='Resume search', command=lambda: self._start('resume')).grid(row=2, column=0, columnspan=2, sticky='ew', pady=(0, 7))
        ttk.Button(actions, text='View log', command=self._attach).grid(row=3, column=0, sticky='ew', padx=(0, 4))
        ttk.Button(actions, text='Stop search', command=self._stop).grid(row=3, column=1, sticky='ew', padx=(4, 0))
        ttk.Button(actions, text='Merge results', command=self._merge_results).grid(row=4, column=0, columnspan=2, sticky='ew', pady=(7, 0))

        right = tk.Frame(outer, background='#eef2f7')
        right.grid(row=1, column=1, sticky='nsew')
        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1)
        status_box = ttk.Frame(right, padding=(18, 12))
        status_box.grid(row=0, column=0, sticky='ew', pady=(0, 12))
        ttk.Label(status_box, textvariable=self.status, style='Section.TLabel', wraplength=580).pack(anchor='w')
        cards = tk.Frame(right, background='#eef2f7')
        cards.grid(row=1, column=0, sticky='ew', pady=(0, 12))
        for index, (key, label) in enumerate([('nodes', 'Reported nodes'), ('hits', 'Hits found'), ('batches', 'Completed batches')]):
            cards.columnconfigure(index, weight=1, uniform='metric')
            card = ttk.Frame(cards, padding=(16, 12))
            card.grid(row=0, column=index, sticky='ew', padx=(0 if index == 0 else 6, 0 if index == 2 else 6))
            ttk.Label(card, text=label, style='Muted.TLabel').pack(anchor='w')
            ttk.Label(card, textvariable=self.metrics[key], style='Value.TLabel').pack(anchor='w', pady=(4, 0))
        events = ttk.Frame(right, padding=16)
        events.grid(row=2, column=0, sticky='nsew')
        events.columnconfigure(0, weight=1)
        events.rowconfigure(2, weight=1)
        toolbar = ttk.Frame(events)
        toolbar.grid(row=0, column=0, sticky='ew')
        ttk.Label(toolbar, text='Run events', style='Section.TLabel').pack(side='left')
        ttk.Button(toolbar, text='Open folder', command=self._open_folder).pack(side='right')
        ttk.Button(toolbar, text='Clear view', command=self._clear_events).pack(side='right', padx=(0, 8))
        self.source_label = ttk.Label(events, textvariable=self.log_source, style='Muted.TLabel')
        self.source_label.grid(row=1, column=0, sticky='ew', pady=(8, 10))
        events.bind('<Configure>', lambda event: self.source_label.configure(wraplength=max(200, event.width - 40)))
        self.log = scrolledtext.ScrolledText(events, state='disabled', wrap='word', font=('Consolas', 10),
                                             background='#f8fafc', foreground='#334155', relief='flat', borderwidth=0,
                                             padx=12, pady=12, spacing1=3, spacing3=5, width=40, height=12,
                                             selectbackground='#dbeafe', selectforeground='#172b49')
        self.log.grid(row=2, column=0, sticky='nsew')
        for tag, color in [('hit', '#087443'), ('error', '#bd3344'), ('finish', '#175ac2'), ('muted', '#768499')]:
            self.log.tag_configure(tag, foreground=color)
        ttk.Label(events, text='Latest 800 lines', style='Muted.TLabel').grid(row=3, column=0, sticky='w', pady=(10, 0))

    @staticmethod
    def _field(parent, label, variable):
        ttk.Label(parent, text=label).pack(anchor='w', pady=(7, 4))
        ttk.Entry(parent, textvariable=variable).pack(fill='x')

    def _scroll_settings(self, event):
        x, y = self.winfo_pointerx(), self.winfo_pointery()
        canvas = self.settings_canvas
        if (canvas.winfo_rootx() <= x < canvas.winfo_rootx() + canvas.winfo_width()
                and canvas.winfo_rooty() <= y < canvas.winfo_rooty() + canvas.winfo_height()):
            if self.settings.winfo_height() > self.settings_canvas.winfo_height():
                canvas.yview_scroll(-int(event.delta / 120) or (-1 if event.delta > 0 else 1), 'units')
            return 'break'

    def _mode_changed(self):
        if self.mode.get() == 'shard':
            self.shard_frame.pack(fill='x', before=self.advanced_toggle)
        else:
            self.shard_frame.pack_forget()

    def _toggle_advanced(self):
        if self.show_advanced.get():
            self.advanced_frame.pack(fill='x', before=self.output_section)
        else:
            self.advanced_frame.pack_forget()

    def _shard_summary(self, *_):
        try:
            count = int(self.shards.get())
            selected = shard_selection(self.owned_display.get(), count).split(',')
            self.shard_hint.set('{v0} shards total, {v1} assigned'.format(v0=count, v1=len(selected)))
        except ValueError:
            self.shard_hint.set('Check the total shard count and assigned IDs.')

    def _choose_folder(self):
        path = filedialog.askdirectory(parent=self, title='Select output directory')
        if path:
            self.directory.set(path)

    def _clear_events(self):
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.configure(state='disabled')

    def _update_metrics(self, counts, batches=None):
        for key in ('nodes', 'hits'):
            if key in counts:
                value = counts[key]
                self.metrics[key].set(f'{value:,}' if isinstance(value, int) else str(value))
        if batches is not None:
            self.metrics['batches'].set(f'{batches:,}' if isinstance(batches, int) else str(batches))

    def _load_preferences(self):
        try:
            data = json.loads(PREFS_FILE.read_text(encoding='utf-8'))
            for name in ('bound', 'workers', 'mode', 'shards', 'owned', 'depth', 'chunksize', 'device', 'directory'):
                if isinstance(data.get(name), str):
                    getattr(self, name).set(data[name])
            selected = owned_ids(self.owned.get(), int(self.shards.get()))
            if selected:
                if len(selected) > 1 and selected == list(range(selected[0], selected[-1] + 1)):
                    self.owned_display.set(f'{selected[0]}-{selected[-1]}')
                else:
                    self.owned_display.set(','.join(str(n) for n in selected))
            self._mode_changed()
        except (OSError, ValueError, AttributeError):
            pass

    def _save_preferences(self):
        try:
            data = {name: getattr(self, name).get() for name in
                    ('bound', 'workers', 'mode', 'shards', 'owned', 'depth', 'chunksize', 'device', 'directory')}
            temp = PREFS_FILE.with_suffix('.tmp')
            temp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            os.replace(temp, PREFS_FILE)
        except OSError:
            pass  # preferences are never required for computation

    def _restore_view(self):
        if self.config_now is not None:
            return
        try:
            cfg = self._cfg()
            if log_filename(cfg).exists() or marker_filename(cfg).exists():
                self._display_log(cfg)
                self.record = self._read_marker(cfg)
                if active_pid(self.record):
                    self.status.set('Running · PID {v0} · Reconnected'.format(v0=self.record['pid']))
                else:
                    self.status.set('Previous log loaded')
        except (OSError, ValueError):
            pass

    def _cfg(self):
        if self.mode.get() == 'shard':
            self.owned.set(shard_selection(self.owned_display.get(), int(self.shards.get())))
        return make_config(self.bound.get(), self.workers.get(), self.mode.get() == 'shard',
                           self.shards.get(), self.owned.get(), self.depth.get(),
                           self.chunksize.get(), self.device.get(), self.directory.get())

    def _engine(self):
        if getattr(sys, 'frozen', False):
            executable = APP_DIR / 'SpoofEngine' / ('SpoofEngine.exe' if os.name == 'nt' else 'SpoofEngine')
            if not executable.is_file():
                raise FileNotFoundError('Missing SpoofEngine executable; use the complete portable package')
            return executable
        return None

    def _line(self, value, record=None):
        self.log.configure(state='normal')
        event = record.get('event') if record else None
        tag = ('hit' if event in ('HIT', 'HIT_FOUND') else
               'error' if event in ('ERROR', 'INTERRUPTED', 'UNRESOLVED') else
               'finish' if event == 'FINISH' else
               'muted' if event == 'CHECKPOINT' else '')
        self.log.insert('end', value + '\n', tag)
        total = int(self.log.index('end-1c').split('.')[0])
        if total > VIEW_LINES + 100:
            self.log.delete('1.0', f'{total - VIEW_LINES}.0')
        self.log.see('end')
        self.log.configure(state='disabled')

    def _display_log(self, cfg, offset=None):
        self.config_now = cfg
        self._clear_events()
        for metric in self.metrics.values():
            metric.set('—')
        self.tail.select(log_filename(cfg), offset=offset)
        self.log_source.set('{v0} · {v1}'.format(v0='History' if offset is None else 'Current run', v1=log_filename(cfg)))

    def _read_marker(self, cfg):
        try:
            return json.loads(marker_filename(cfg).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None

    def _write_marker(self, cfg, record):
        try:
            path = marker_filename(cfg)
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_name(path.name + '.tmp')
            temp.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
            os.replace(temp, path)
        except OSError:
            self._line('[GUI notice] Cannot save process association; search continues.')

    def _start(self, action):
        try:
            cfg = self._cfg()
            previous = self._read_marker(cfg)
            if active_pid(previous):
                self.record = previous
                self._display_log(cfg)
                messagebox.showwarning('Search already running', 'This shard is already running (PID={v0}).'.format(v0=previous['pid']))
                return
            if action == 'fresh' and not messagebox.askyesno('Confirm new search', "Starting a new search removes this task's checkpoints. Existing logs are retained.\nContinue?"):
                return
            argv = launch_args(cfg, action, self._engine())
            self._save_preferences()
            cfg['output_dir'].mkdir(parents=True, exist_ok=True)
            # Capture before launch: fast jobs may finish before Popen returns.
            try:
                log_offset = log_filename(cfg).stat().st_size
            except FileNotFoundError:
                log_offset = 0
            flags = 0
            popen_args = dict(cwd=str(APP_DIR), stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              close_fds=True)
            if os.name == 'nt':
                flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
                popen_args['creationflags'] = flags
            else:
                popen_args['start_new_session'] = True
            errors = None
            try:
                errors = (cfg['output_dir'] / 'engine_stderr.log').open('ab')
                popen_args['stderr'] = errors
            except OSError:
                pass
            try:
                self.proc = subprocess.Popen(argv, **popen_args)
            finally:
                if errors is not None:
                    errors.close()
            self.record = {'pid': self.proc.pid, 'birth_token': process_birth_token(self.proc.pid),
                           'bound': str(cfg['bound']), 'tag': cfg['tag']}
            self._write_marker(cfg, self.record)
            self._display_log(cfg, offset=log_offset)
            self.status.set('Running · PID {v0} · {v1}'.format(v0=self.proc.pid, v1='FULL' if not cfg['tag'] else 'SHARDED'))
        except (ValueError, OSError) as err:
            messagebox.showerror('Cannot start search', str(err))

    def _attach(self):
        try:
            cfg = self._cfg()
            self._save_preferences()
            self.proc = None
            self._display_log(cfg)
            self.record = self._read_marker(cfg)
            if active_pid(self.record):
                self.status.set('Running · PID {v0} · Reconnected'.format(v0=self.record['pid']))
            else:
                self.status.set('History opened · No running process found')
        except (ValueError, OSError) as err:
            messagebox.showerror('Invalid settings', str(err))

    def _stop(self):
        record = self.record
        if not active_pid(record):
            messagebox.showinfo('No running process', 'No running process matches this session.')
            return
        if not messagebox.askyesno('Confirm stop', 'The engine and its workers will stop without guaranteeing a new checkpoint.\nResume will recompute uncommitted batches from the latest valid checkpoint. Continue?'):
            return
        pid = record['pid']
        try:
            if os.name == 'nt':
                result = subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'],
                                        capture_output=True, timeout=15, check=False,
                                        creationflags=subprocess.CREATE_NO_WINDOW)
                if result.returncode != 0:
                    encoding = f'cp{ctypes.windll.kernel32.GetOEMCP()}'
                    detail = result.stderr.decode(encoding, errors='replace').strip()
                    # taskkill also reports children that exited while the tree
                    # was being enumerated. The stop succeeded if the root is gone.
                    time.sleep(0.2)
                    if active_pid(record):
                        raise OSError(detail or 'taskkill failed')
            else:
                os.killpg(pid, signal.SIGKILL)
            self.status.set('Stop requested · Resume is available')
        except (OSError, subprocess.TimeoutExpired) as err:
            messagebox.showerror('Failed to stop', str(err))

    def _merge_results(self):
        from merge_dialog import MergeDialog
        window = getattr(self, '_merge_window', None)
        if window is not None and window.winfo_exists():
            window.lift()
            window.focus_set()
        else:
            self._merge_window = MergeDialog(self)

    def _open_folder(self):
        try:
            cfg = self._cfg()
            path = cfg['output_dir']
            path.mkdir(parents=True, exist_ok=True)
            if os.name == 'nt':
                os.startfile(str(path))
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', str(path)])
            else:
                subprocess.Popen(['xdg-open', str(path)])
        except (ValueError, OSError) as err:
            messagebox.showerror('Cannot open directory', str(err))

    def _tick(self):
        try:
            if self.config_now:
                for line, record in self.tail.poll():
                    self._line(line, record)
                    if record and record.get('event') in ('START', 'RESUME'):
                        counts = record['checkpoint_counters']
                        self._update_metrics(counts, record['checkpoint_done'])
                    elif record and record.get('event') == 'PROGRESS':
                        self._update_metrics({'nodes': record.get('nodes', 0),
                                              'hits': record.get('hits', 0)},
                                             record.get('batches_done'))
                    elif record and record.get('event') == 'HIT_FOUND':
                        if 'hits' in record:
                            self._update_metrics({'hits': record['hits']})
                    elif record and record.get('event') == 'FINISH':
                        self._update_metrics(record.get('counters', {}), record.get('completed_batches'))
                        self.status.set('Search finished · {v0}'.format(v0=record.get('status', '?')))
            if self.proc is not None and self.proc.poll() is not None and not str(self.status.get()).startswith('Search finished'):
                if self.proc.returncode != 0:
                    self.status.set('Engine exited (code {v0}) · Check engine_stderr.log'.format(v0=self.proc.returncode))
                else:
                    self.status.set('Engine finished · See run log')
            elif self.record and not active_pid(self.record) and not str(self.status.get()).startswith('Search finished'):
                self.status.set('Not running · Resume or view log')
            elif self.record and active_pid(self.record) and str(self.status.get()).startswith('Not running'):
                self.status.set('Running · PID {v0}'.format(v0=self.record['pid']))
        except Exception as err:
            self._line('[GUI warning] Log display error: {v0}; search is unaffected'.format(v0=type(err).__name__))
        finally:
            self.after(POLL_MS, self._tick)

    def _close(self):
        self.destroy()  # never terminate the separate search process


def main():
    App().mainloop()


if __name__ == '__main__':
    main()
