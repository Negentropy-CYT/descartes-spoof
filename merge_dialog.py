"""Desktop result selection and merge, using the shared offline validator."""
from pathlib import Path
from queue import Queue, Empty
from threading import Thread
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from merge_shards import inspect_results, save_merged


class MergeDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title('Merge results')
        self.geometry('840x580')
        self.minsize(640, 450)
        self.transient(parent)
        self.paths = []
        self.busy = False
        self.valid = False
        self.queue = Queue()
        self.configure(background='#eef2f7')
        style = ttk.Style(self)
        style.configure('Merge.Treeview', rowheight=28, background='#ffffff', fieldbackground='#ffffff', foreground='#23324a')
        style.configure('Merge.Treeview.Heading', background='#f1f5fa', foreground='#617187', padding=6)
        panel = ttk.Frame(self, padding=20)
        panel.pack(fill='both', expand=True, padx=14, pady=14)
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(2, weight=1)
        ttk.Label(panel, text='Merge results', style='Section.TLabel').grid(row=0, column=0, sticky='w', pady=(0, 12))
        toolbar = ttk.Frame(panel)
        toolbar.grid(row=1, column=0, sticky='ew', pady=(0, 12))
        self.add_button = ttk.Button(toolbar, text='Add results…', command=self._add)
        self.add_button.pack(side='left')
        self.remove_button = ttk.Button(toolbar, text='Remove', command=self._remove)
        self.remove_button.pack(side='left', padx=(8, 0))
        table = ttk.Frame(panel)
        table.grid(row=2, column=0, sticky='nsew')
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=('path', 'owned', 'status'), show='headings', selectmode='extended', height=8, style='Merge.Treeview')
        for name, title, width in [('path', 'Result file', 470), ('owned', 'Shard IDs', 130), ('status', 'Status', 80)]:
            self.tree.heading(name, text=title)
            self.tree.column(name, width=width, minwidth=70, stretch=name == 'path')
        self.tree.grid(row=0, column=0, sticky='nsew')
        vertical = tk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal = tk.Scrollbar(table, orient='horizontal', command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky='ew')
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.summary = tk.StringVar(value='No results added')
        ttk.Label(panel, textvariable=self.summary, wraplength=650).grid(row=3, column=0, sticky='w', pady=(12, 8))
        self.messages = scrolledtext.ScrolledText(panel, height=4, wrap='word', state='disabled',
                                                 font=('Segoe UI', 10), background='#f8fafc',
                                                 foreground='#334155', borderwidth=0, padx=8, pady=8)
        self.messages.grid(row=4, column=0, sticky='ew')
        footer = ttk.Frame(panel)
        footer.grid(row=5, column=0, sticky='ew', pady=(14, 0))
        self.save_button = ttk.Button(footer, text='Merge and save…', style='Primary.TButton', command=self._save, state='disabled')
        self.save_button.pack(side='right')
        ttk.Button(footer, text='Close', command=self.destroy).pack(side='right', padx=(0, 8))
        self.poll_id = self.after(100, self._poll)

    def destroy(self):
        if getattr(self, 'poll_id', None):
            self.after_cancel(self.poll_id)
            self.poll_id = None
        super().destroy()

    def _message(self, text):
        self.messages.configure(state='normal')
        self.messages.delete('1.0', 'end')
        self.messages.insert('end', text)
        self.messages.configure(state='disabled')

    def _job(self, operation, *args):
        self.busy = True
        for button in (self.add_button, self.remove_button, self.save_button):
            button.configure(state='disabled')
        self._message('Processing…')
        def work():
            try:
                self.queue.put((operation, operation(*args), None))
            except Exception as error:
                self.queue.put((operation, None, str(error)))
        Thread(target=work, daemon=True).start()

    def _add(self):
        if self.busy:
            return
        chosen = filedialog.askopenfilenames(parent=self, title='Add results', filetypes=[('Result JSON', '*.json')])
        if not chosen:
            return
        for name in chosen:
            path = Path(name).resolve()
            if path not in self.paths:
                self.paths.append(path)
        self._job(inspect_results, self.paths.copy())

    def _remove(self):
        if self.busy:
            return
        indices = {int(i) for i in self.tree.selection()}
        if indices:
            self.paths = [p for i, p in enumerate(self.paths) if i not in indices]
            self._job(inspect_results, self.paths.copy())

    def _save(self):
        if self.busy or not self.valid:
            return
        name = filedialog.asksaveasfilename(parent=self, title='Save merged result', initialdir=str(self.paths[0].parent),
                                          initialfile='merged.json', defaultextension='.json', filetypes=[('Result JSON', '*.json')])
        if name:
            self._job(save_merged, self.paths.copy(), Path(name))

    def _poll(self):
        try:
            operation, result, error = self.queue.get_nowait()
        except Empty:
            pass
        else:
            self.busy = False
            self.add_button.configure(state='normal')
            self.remove_button.configure(state='normal')
            if error:
                self.valid = False
                self._message(error)
            elif operation is inspect_results:
                self.tree.delete(*self.tree.get_children())
                for i, entry in enumerate(result['entries']):
                    self.tree.insert('', 'end', iid=str(i), values=(entry['path'], entry['owned'], entry['status']))
                identity = result['identity']
                if identity:
                    bound = identity['bound']
                    if bound.startswith('1') and len(bound) > 1 and set(bound[1:]) == {'0'}:
                        bound = '1e' + str(len(bound) - 1)
                    self.summary.set('Bound {v0} · Coverage {v1}/{v2} · {v3} files'.format(v0=bound, v1=result['covered'], v2=identity['shards'], v3=len(self.paths)))
                else:
                    self.summary.set('{v0} files'.format(v0=len(self.paths)))
                self.valid = result['merged'] is not None
                self._message('\n'.join(result['errors']) if result['errors'] else 'Checks passed. Ready to merge.')
            else:
                self._message('Merged · Nodes {v0} · Hits {v1}'.format(v0=f"{result['counters']['nodes']:,}", v1=result['counters']['hits']))
                messagebox.showinfo('Merge complete', 'Merged result saved.', parent=self)
            self.save_button.configure(state='normal' if self.valid else 'disabled')
        self.poll_id = self.after(100, self._poll)
