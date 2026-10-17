"""Small Tk desktop UI. All business operations remain in existing backend modules."""
import datetime as dt
import json
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .controller import ManagerError


class ManagerWindow:
    def __init__(self, root, controller, settings=None, initial=None, *, demo=False):
        self.root, self.controller, self.settings = root, controller, settings
        self.demo = demo
        self.busy = self.closed = False
        self.thread = None
        self.completion_timer = None
        self.mailbox = queue.Queue()
        self.actions, self.values, self.metrics = [], {}, {}
        self.investigation_rows = []
        self.selected_batch = None
        self.current_workspace = ''
        self.root.title('Biometric Sync Manager' + (' — DEMO' if demo else ''))
        self.root.geometry('1160x900')
        self.root.minsize(960, 800)
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('.', font=('Segoe UI', 10), background='#f4f6f9')
        style.configure('TFrame', background='#f4f6f9')
        style.configure('TLabel', background='#f4f6f9', foreground='#26364a')
        style.configure('Title.TLabel', font=('Segoe UI', 19, 'bold'), foreground='#142b48')
        style.configure('Metric.TLabel', font=('Segoe UI', 16, 'bold'), foreground='#142b48')
        style.configure('TButton', padding=(10, 6))
        style.configure('TNotebook.Tab', padding=(18, 9))
        style.configure('Treeview', rowheight=27, fieldbackground='white', background='white')
        style.configure('Treeview.Heading', font=('Segoe UI', 10, 'bold'))
        outer = ttk.Frame(root, padding=18)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='Biometric Sync Manager', style='Title.TLabel').pack(anchor='w')
        ttk.Label(outer, text='Demonstration data · no live connections' if demo else
                  'Manual monitoring, verified investigation and recovery review').pack(anchor='w', pady=(2, 14))
        self.tabs = ttk.Notebook(outer)
        self.tabs.pack(fill='both', expand=True)
        dashboard = ttk.Frame(self.tabs, padding=14)
        history = ttk.Frame(self.tabs, padding=14)
        recovery = ttk.Frame(self.tabs, padding=14)
        for frame, label in ((dashboard, 'Dashboard'), (history, 'Historical investigation'), (recovery, 'Recovery management')):
            self.tabs.add(frame, text=label)
        self._dashboard(dashboard)
        self._history(history)
        self._recovery(recovery)
        footer = ttk.Frame(outer)
        # Reserve the footer before the expanding notebook, including smaller displays.
        footer.pack(side='bottom', before=self.tabs, fill='x', pady=(10, 0))
        self.status = tk.StringVar(value='Ready. Actions run only when selected; the Windows service remains independent.')
        ttk.Label(footer, textvariable=self.status, wraplength=920).pack(side='left', fill='x', expand=True)
        if settings and not demo:
            self._button(footer, 'Remember paths', self.save_settings).pack(side='right')
        defaults = dict(start=dt.datetime.now().strftime('%Y-%m-%d 00:00:00'),
                        end=dt.datetime.now().strftime('%Y-%m-%d 23:59:59'), site='https://erp.myfpf.com')
        defaults.update(settings.load() if settings and not demo else {})
        defaults.update(initial or {})
        for name, value in defaults.items():
            if name in self.values:
                self.values[name].set(value)
        if demo:
            self.values['api_key'].set('DEMO')
            self.values['api_secret'].set('DEMO')
            self.backup_ack.set(True)

    def _button(self, parent, text, callback):
        button = ttk.Button(parent, text=text, command=callback)
        self.actions.append(button)
        return button

    def _field(self, parent, label, name, row, picker=None, secret=False):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky='w', padx=(0, 12), pady=3)
        variable = self.values.setdefault(name, tk.StringVar())
        entry = ttk.Entry(parent, textvariable=variable, show='*' if secret else '')
        entry.grid(row=row, column=1, sticky='ew', pady=3)
        if picker:
            def choose():
                selected = filedialog.askdirectory(parent=self.root) if picker == 'directory' else filedialog.askopenfilename(parent=self.root)
                if selected:
                    variable.set(selected)
            ttk.Button(parent, text='Browse…', command=choose).grid(row=row, column=2, padx=(8, 0))
        parent.columnconfigure(1, weight=1)
        return entry

    def _table(self, parent, columns, height=8, selectmode='browse'):
        frame = ttk.Frame(parent)
        frame.pack(fill='both', expand=True, pady=(8, 0))
        tree = ttk.Treeview(frame, columns=[key for key, _ in columns], show='headings', height=height, selectmode=selectmode)
        vertical = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
        horizontal = ttk.Scrollbar(frame, orient='horizontal', command=tree.xview)
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        tree.grid(row=0, column=0, sticky='nsew')
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        for key, label in columns:
            tree.heading(key, text=label)
            tree.column(key, width=160 if key != 'reason' else 300, minwidth=70)
        return tree

    @staticmethod
    def _rows(tree, rows, columns, id_field=None):
        tree.delete(*tree.get_children())
        for index, row in enumerate(rows[:1000]):
            tree.insert('', 'end', iid=str(row[id_field]) if id_field else str(index),
                        values=[row.get(key, '') for key in columns])

    def _dashboard(self, frame):
        form = ttk.Frame(frame)
        form.pack(fill='x')
        self._field(form, 'Monitoring ledger · read-only', 'monitor_ledger', 0, 'file')
        self._button(frame, 'Refresh dashboard', self.refresh_dashboard).pack(anchor='w', pady=10)
        grid = ttk.Frame(frame)
        grid.pack(fill='x', pady=(0, 12))
        for index, (key, label) in enumerate((('service', 'Windows service'), ('integrity', 'SQLite integrity'),
                ('total', 'Total records'), ('pending', 'Pending'), ('retry', 'Retry'),
                ('failed', 'Failed / quarantined'), ('holds', 'Historical holds'))):
            cell = ttk.Frame(grid, padding=(8, 10))
            cell.grid(row=index // 4, column=index % 4, sticky='ew')
            grid.columnconfigure(index % 4, weight=1)
            ttk.Label(cell, text=label).pack(anchor='w')
            self.metrics[key] = tk.StringVar(value='—')
            ttk.Label(cell, textvariable=self.metrics[key], style='Metric.TLabel').pack(anchor='w')
        self.delivery = tk.StringVar(value='Last successful delivery: not loaded')
        ttk.Label(frame, textvariable=self.delivery).pack(anchor='w')
        ttk.Label(frame, text='Device rows are recorded fetch observations, not live connectivity. Failed/quarantined includes held/rejected records.').pack(anchor='w', pady=(6, 0))
        self.device_table = self._table(frame, [('device', 'Device'), ('fetch', 'Last fetch result'),
            ('last_fetch', 'Last successful fetch'), ('backlog', 'Backlog'), ('last_delivery', 'Last delivery · UTC')])

    def refresh_dashboard(self):
        ledger = self.values['monitor_ledger'].get()
        self._start(lambda: self.controller.dashboard(ledger), self._dashboard_loaded)

    def _dashboard_loaded(self, data):
        for key, variable in self.metrics.items():
            variable.set(str(data[key]))
        self.delivery.set('Last successful delivery · UTC: ' + data['last_delivery'])
        self._rows(self.device_table, data['devices'], ('device', 'fetch', 'last_fetch', 'backlog', 'last_delivery'))
        selected = {self.devices.get(i) for i in self.devices.curselection()}
        ids = sorted({'FP1_DEVICE_01', 'FP2_DEVICE_08', 'FP2_DEVICE_11'} | {d['device'] for d in data['devices']})
        self.devices.delete(0, 'end')
        for index, device in enumerate(ids):
            self.devices.insert('end', device)
            if device in selected:
                self.devices.selection_set(index)
        self.status.set(' '.join(data['errors']) or 'Dashboard refreshed from read-only diagnostics.')

    def _history(self, frame):
        form = ttk.Frame(frame)
        form.pack(fill='x')
        for row, label, name, picker in ((0, 'Protected investigation backup', 'backup', 'file'),
                (1, 'Start · site local time', 'start', None), (2, 'End · inclusive', 'end', None)):
            self._field(form, label, name, row, picker)
        ttk.Label(form, text='Devices · Ctrl+click for multiple').grid(row=3, column=0, sticky='w')
        self.devices = tk.Listbox(form, height=3, selectmode='extended', exportselection=False, font=('Segoe UI', 10))
        self.devices.grid(row=3, column=1, sticky='ew', pady=3)
        for name in ('FP1_DEVICE_01', 'FP2_DEVICE_08', 'FP2_DEVICE_11'):
            self.devices.insert('end', name)
        self.devices.selection_set(0)
        self._field(form, 'ERPNext HTTPS origin', 'site', 4)
        self._field(form, 'Reviewed verification contract', 'contract', 5, 'file')
        self._field(form, 'Independently approved SHA256', 'contract_hash', 6)
        self._field(form, 'Protected report directory', 'output', 7, 'directory')
        self._field(form, 'API key · memory only', 'api_key', 8, secret=True)
        self._field(form, 'API secret · memory only', 'api_secret', 9, secret=True)
        self.backup_ack, self.production_ack = tk.BooleanVar(), tk.BooleanVar()
        ttk.Checkbutton(frame, text='I confirm this input is a protected offline SQLite backup.', variable=self.backup_ack).pack(anchor='w', pady=(6, 0))
        ttk.Checkbutton(frame, text='I acknowledge production read-only compatibility at the entered origin; results remain unverified.', variable=self.production_ack).pack(anchor='w')
        actions = ttk.Frame(frame)
        actions.pack(fill='x', pady=(7, 0))
        self._button(actions, 'Run investigation & export', self.run_investigation).pack(side='left')
        ttk.Label(actions, text='32 GETs · 120-second deadline · 10-second timeout').pack(side='left', padx=12)
        self.investigation_status = tk.StringVar(value='No investigation loaded.')
        ttk.Label(frame, textvariable=self.investigation_status, wraplength=1060).pack(anchor='w', pady=(8, 0))
        filtering = ttk.Frame(frame)
        filtering.pack(fill='x', pady=(5, 0))
        self.category, self.search = tk.StringVar(value='All classifications'), tk.StringVar()
        self.category_box = ttk.Combobox(filtering, textvariable=self.category, state='readonly', values=['All classifications'], width=27)
        self.category_box.pack(side='left')
        self.category_box.bind('<<ComboboxSelected>>', lambda _: self.filter_results())
        ttk.Label(filtering, text='Search').pack(side='left', padx=(12, 6))
        ttk.Entry(filtering, textvariable=self.search).pack(side='left', fill='x', expand=True)
        self.search.trace_add('write', lambda *_: self.filter_results())
        self.result_columns = ('id', 'device_id', 'punch_timestamp', 'user_id', 'employee', 'classification', 'reason')
        self.result_table = self._table(frame, list(zip(self.result_columns,
            ('Record', 'Device', 'Source time', 'Device user ID', 'Employee', 'Classification', 'Reason'))), height=5)

    def run_investigation(self):
        values = {key: variable.get() for key, variable in self.values.items()}
        devices = [self.devices.get(i) for i in self.devices.curselection()]
        key, secret = values.pop('api_key'), values.pop('api_secret')
        self.values['api_key'].set('')
        self.values['api_secret'].set('')
        production_ack, backup_ack = self.production_ack.get(), self.backup_ack.get()
        self.investigation_rows = []
        self.filter_results()
        self.investigation_status.set('Investigation in progress. Previous results cleared.')
        self._start(lambda: self.controller.investigate(values, devices, key, secret, production_ack, backup_ack), self._investigation_loaded)

    def _investigation_loaded(self, data):
        summary = data['summary']
        self.investigation_rows = data['rows']
        state = 'UNVERIFIED' if summary['status'] != 'complete' else 'Investigation complete'
        if summary.get('purpose') == 'production_read_only_compatibility':
            state += ' · production compatibility only; not a recovery candidate report'
        counts = ', '.join(f'{key.replace("_", " ")}: {value}' for key, value in summary['totals'].items() if value)
        self.investigation_status.set(state + ' | ' + counts + ' | ' + '; '.join(summary.get('blockers', [])))
        self.category_box.configure(values=['All classifications'] + sorted({r['classification'] for r in data['rows']}))
        self.category.set('All classifications')
        self.filter_results()
        self.status.set('Protected CSV and JSON exported to the selected report directory.')

    def filter_results(self):
        if not hasattr(self, 'result_table'):
            return
        query, category = self.search.get().casefold(), self.category.get()
        rows = [r for r in self.investigation_rows if (category == 'All classifications' or r['classification'] == category)
                and (not query or any(query in str(r.get(k, '')).casefold() for k in self.result_columns))]
        self._rows(self.result_table, rows, self.result_columns)
        if len(rows) > 1000:
            self.status.set(f'{len(rows)} matching rows; displaying first 1000. Protected exports include all rows.')

    def _recovery(self, frame):
        form = ttk.Frame(frame)
        form.pack(fill='x')
        self._field(form, 'Existing protected recovery workspace', 'workspace', 0, 'directory')
        self._field(form, 'Synthetic destination fixture', 'simulation', 1, 'file')
        self._field(form, 'Synthetic normal-worker lock path', 'normal_lock', 2)
        self._field(form, 'Approval review reference', 'approval_reference', 3)
        controls = ttk.Frame(frame)
        controls.pack(fill='x', pady=8)
        self._button(controls, 'List existing batches', self.list_batches).pack(side='left')
        self.approve_button = self._button(controls, 'Approve selected records', self.approve_selected)
        self.approve_button.pack(side='left', padx=8)
        self.simulate_button = self._button(controls, 'Run simulation only', self.run_simulation)
        self.simulate_button.pack(side='left')
        self._button(controls, 'Export batch audit', self.export_batch).pack(side='right')
        self.batch_status = tk.StringVar(value='Live recovery is unavailable. Load an existing batch to review its pinned inputs and audit.')
        ttk.Label(frame, textvariable=self.batch_status, wraplength=1060).pack(anchor='w')
        self.batch_table = self._table(frame, [('id', 'Batch'), ('created', 'Prepared'), ('fingerprint', 'Pinned fingerprint')], height=4)
        self.batch_table.bind('<<TreeviewSelect>>', lambda _: self.load_batch())
        self.item_columns = ('id', 'device', 'time', 'employee', 'status', 'reason')
        self.item_table = self._table(frame, list(zip(self.item_columns, ('Record', 'Source device', 'Source time', 'Employee', 'Status', 'Reason'))), height=6, selectmode='extended')
        self.item_table.bind('<<TreeviewSelect>>', lambda _: self._recovery_buttons())
        self.event_table = self._table(frame, [('at', 'Audit time'), ('item', 'Record'), ('status', 'Status'), ('reason', 'Outcome'), ('reference', 'Review / destination reference')], height=5)
        self._recovery_buttons()

    def list_batches(self):
        workspace = self.values['workspace'].get()
        self.selected_batch = None
        self._rows(self.item_table, [], self.item_columns)
        self._rows(self.event_table, [], ('at', 'item', 'status', 'reason', 'reference'))
        def loaded(rows):
            self.current_workspace = workspace
            self._rows(self.batch_table, rows, ('id', 'created', 'fingerprint'), 'id')
            note = ' First 1000 shown.' if len(rows) > 1000 else ''
            self.batch_status.set(f'{len(rows)} existing batches. Select one to review records and audit events.' + note)
        self._start(lambda: self.controller.batches(workspace), loaded)

    def load_batch(self):
        selected = self.batch_table.selection()
        if not self.busy and selected:
            self._start(lambda: self.controller.batch(self.current_workspace, selected[0]), self._batch_loaded)

    def _batch_loaded(self, data):
        self.selected_batch = data
        items = []
        for item in data['items']:
            source = json.loads(item['source'])
            items.append(dict(item, device=source['device_id'], time=source['punch_timestamp']))
        self._rows(self.item_table, items, self.item_columns, 'id')
        self._rows(self.event_table, data['events'], ('at', 'item', 'status', 'reason', 'reference'))
        note = ' · first 1000 rows per table; export the full audit' if max(len(items), len(data['events'])) > 1000 else ''
        self.batch_status.set(' | '.join(f'{k}: {v}' for k, v in data['counts'].items()) + ' · live recovery unavailable' + note)
        self._recovery_buttons()

    def _recovery_buttons(self):
        if not hasattr(self, 'approve_button'):
            return
        pending = {str(r['id']) for r in self.selected_batch['items'] if r['status'] == 'Pending Approval'} if self.selected_batch else set()
        selected = set(self.item_table.selection())
        self.approve_button.configure(state='normal' if not self.busy and selected and selected <= pending else 'disabled')
        synthetic = self.selected_batch and self.selected_batch['metadata']['contract'].get('destination_environment') == 'synthetic'
        actionable = self.selected_batch and any(r['status'] in ('Approved', 'Needs Review') for r in self.selected_batch['items'])
        self.simulate_button.configure(state='normal' if not self.busy and synthetic and actionable else 'disabled')

    def approve_selected(self):
        if not self.selected_batch:
            return
        ids = [int(value) for value in self.item_table.selection()]
        reference = self.values['approval_reference'].get().strip()
        if not ids or not reference:
            self.status.set('Select Pending Approval rows and enter an explicit review reference.')
            return
        if messagebox.askyesno('Approve selected records', f'Approve {len(ids)} selected record(s) in this pinned batch?\nThis does not execute recovery.', parent=self.root):
            data, workspace = self.selected_batch, self.current_workspace
            self._start(lambda: self.controller.approve(workspace, data['batch'], data['fingerprint'], ids, reference), self._batch_loaded)

    def run_simulation(self):
        if not self.selected_batch:
            return
        if messagebox.askyesno('Run simulation', 'Execute the approved records against the synthetic fixture only?', parent=self.root):
            data, workspace = self.selected_batch, self.current_workspace
            scenario, lock = self.values['simulation'].get(), self.values['normal_lock'].get()
            self._start(lambda: self.controller.simulate(workspace, data['batch'], data['fingerprint'], scenario, lock), self._batch_loaded)

    def export_batch(self):
        if not self.selected_batch:
            self.status.set('Select a batch first.')
            return
        output = filedialog.askdirectory(parent=self.root, title='Protected batch report directory')
        if output:
            workspace, batch = self.current_workspace, self.selected_batch['batch']
            self._start(lambda: self.controller.export_recovery(workspace, batch, output),
                        lambda _: self.status.set('Batch CSV and JSON exported to protected storage.'))

    def save_settings(self):
        try:
            self.settings.save({k: v.get() for k, v in self.values.items()})
            self.status.set('App paths remembered. Credentials, acknowledgements and record data were not saved.')
        except Exception:
            self.status.set('App settings could not be saved. Production configuration was not changed.')

    def _start(self, task, completed):
        if self.busy or self.closed:
            return
        self.busy = True
        for button in self.actions:
            button.configure(state='disabled')
        self.status.set('Working on the selected management action…')
        def work():
            try:
                self.mailbox.put((True, task()))
            except BaseException as error:
                # Never display arbitrary filesystem/HTTP exception strings.
                self.mailbox.put((False, str(error) if isinstance(error, ManagerError) else 'management_action_failed'))
        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()
        self.completion_timer = self.root.after(80, lambda: self._receive(completed))

    def _receive(self, completed):
        if self.closed:
            return
        try:
            ok, data = self.mailbox.get_nowait()
        except queue.Empty:
            # UI completion notification only: never polls the service, ledger or API.
            self.completion_timer = self.root.after(80, lambda: self._receive(completed))
            return
        self.busy = False
        for button in self.actions:
            button.configure(state='normal')
        if ok:
            self.status.set('Management action completed.')
            completed(data)
        else:
            self.status.set('Action blocked: ' + data.replace('_', ' ') + '. Review the inputs and verification prerequisites.')
            if not self.investigation_rows:
                self.investigation_status.set('No completed investigation available. Partial report files are not accepted.')
        self._recovery_buttons()

    def close(self):
        if self.closed:
            return
        if self.busy and not messagebox.askyesno('Close manager', 'Cancel this management action and close?\nThe Windows service will continue independently.', parent=self.root):
            return
        self.closed = True
        if self.completion_timer:
            self.root.after_cancel(self.completion_timer)
        self.controller.close()
        self.values['api_key'].set('')
        self.values['api_secret'].set('')
        if self.thread:
            self.thread.join(timeout=3)
        self.root.destroy()
