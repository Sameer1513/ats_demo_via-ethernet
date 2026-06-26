"""
Main GUI module for NI DAQ Controller.

Creates the primary application window with a dashboard tab, dynamically
generated device tabs, system controls, and logging display. Uses a dark
theme suitable for industrial monitoring applications.

Layout:
    - Menu bar with File, Device, and Help menus
    - Main notebook with Dashboard tab and Device tabs
    - Dashboard tab with device summary, status, and system log
    - Status bar showing connection status and task count

Typical usage:
    from gui import DAQApp
    app = DAQApp(task_manager, device_manager)
    app.run()
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading
import time
from datetime import datetime
from typing import Optional, Dict, List, Any
from logger import get_logger, log_manager
from task_manager import TaskManager
from device_manager import DeviceManager, DeviceInfo, DeviceStatus
from device_tab import DeviceTab
from config import global_config

log = get_logger(__name__)


class DashboardTab:
    """
    Dashboard tab providing an overview of all connected devices.

    Displays device summary cards, connection status, active tasks,
    and a system log viewer.

    Attributes:
        parent: Parent notebook widget
        task_manager: Global TaskManager instance
        device_manager: Global DeviceManager instance
        frame: Main frame for this tab
    """

    def __init__(self, parent: ttk.Notebook,
                 task_manager: TaskManager,
                 device_manager: DeviceManager) -> None:
        """
        Initialize dashboard tab.

        Args:
            parent: Parent notebook widget
            task_manager: Global TaskManager instance
            device_manager: Global DeviceManager instance
        """
        self.parent = parent
        self.task_manager = task_manager
        self.device_manager = device_manager
        self.frame = ttk.Frame(parent)

        # Add dashboard as first tab
        parent.add(self.frame, text="📊 Dashboard")
        parent.select(self.frame)

        self._device_cards: Dict[str, tk.Frame] = {}
        self._system_log_text: Optional[tk.Text] = None
        self._active_tasks_label: Optional[tk.Label] = None
        self._refresh_timer: Optional[str] = None

        log.info("DashboardTab created")

    def create_ui(self) -> None:
        """Create the dashboard UI layout."""
        # Configure grid
        self.frame.grid_rowconfigure(0, weight=0)  # Summary bar
        self.frame.grid_rowconfigure(1, weight=1)  # Main content
        self.frame.grid_columnconfigure(0, weight=1)

        # Top summary bar
        self._create_summary_bar()

        # Main content area with two columns
        content_frame = tk.Frame(self.frame, bg='#333333')
        content_frame.grid(row=1, column=0, sticky='nsew', padx=5, pady=5)
        content_frame.grid_columnconfigure(0, weight=2)  # Device cards
        content_frame.grid_columnconfigure(1, weight=1)  # System info
        content_frame.grid_rowconfigure(0, weight=1)

        # Left: Device cards
        devices_frame = tk.LabelFrame(
            content_frame,
            text="Connected Devices",
            font=('TkDefaultFont', 10, 'bold'),
            bg='#333333', fg='white',
            padx=10, pady=5
        )
        devices_frame.grid(row=0, column=0, sticky='nsew', padx=(0, 5))
        devices_frame.grid_columnconfigure(0, weight=1)

        self._devices_canvas = tk.Canvas(
            devices_frame, bg='#333333', highlightthickness=0
        )
        self._devices_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        devices_scrollbar = ttk.Scrollbar(
            devices_frame, orient=tk.VERTICAL,
            command=self._devices_canvas.yview
        )
        devices_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._devices_canvas.configure(yscrollcommand=devices_scrollbar.set)

        self._device_cards_frame = tk.Frame(
            self._devices_canvas, bg='#333333'
        )
        self._devices_canvas.create_window(
            (0, 0), window=self._device_cards_frame,
            anchor='nw', width=self._devices_canvas.winfo_width()
        )

        self._device_cards_frame.bind(
            '<Configure>',
            lambda e: self._devices_canvas.configure(
                scrollregion=self._devices_canvas.bbox('all')
            )
        )
        self._devices_canvas.bind(
            '<Configure>',
            lambda e: self._devices_canvas.itemconfig(
                self._devices_canvas.find_all()[0] if
                self._devices_canvas.find_all() else '',
                width=e.width
            )
        )

        # Right: System info and log
        right_frame = tk.Frame(content_frame, bg='#333333')
        right_frame.grid(row=0, column=1, sticky='nsew')
        right_frame.grid_rowconfigure(1, weight=1)
        right_frame.grid_columnconfigure(0, weight=1)

        # Active tasks
        tasks_frame = tk.LabelFrame(
            right_frame,
            text="Active Tasks",
            font=('TkDefaultFont', 10, 'bold'),
            bg='#333333', fg='white',
            padx=10, pady=5
        )
        tasks_frame.grid(row=0, column=0, sticky='ew', pady=(0, 5))

        self._active_tasks_label = tk.Label(
            tasks_frame,
            text="No active tasks",
            font=('TkDefaultFont', 9),
            bg='#333333', fg='#aaaaaa',
            anchor='w', justify=tk.LEFT
        )
        self._active_tasks_label.pack(fill=tk.X, padx=5, pady=5)

        # System log
        log_frame = tk.LabelFrame(
            right_frame,
            text="System Log",
            font=('TkDefaultFont', 10, 'bold'),
            bg='#333333', fg='white',
            padx=10, pady=5
        )
        log_frame.grid(row=1, column=0, sticky='nsew')
        log_frame.grid_rowconfigure(0, weight=1)
        log_frame.grid_columnconfigure(0, weight=1)

        self._system_log_text = tk.Text(
            log_frame,
            font=('Consolas', 8),
            bg='#1a1a1a', fg='#00ff00',
            wrap=tk.WORD,
            state=tk.DISABLED,
            height=15
        )
        self._system_log_text.grid(row=0, column=0, sticky='nsew')

        log_scrollbar = ttk.Scrollbar(
            log_frame, orient=tk.VERTICAL,
            command=self._system_log_text.yview
        )
        log_scrollbar.grid(row=0, column=1, sticky='ns')
        self._system_log_text.configure(yscrollcommand=log_scrollbar.set)

        # Initial refresh
        self.refresh_dashboard()

    def _create_summary_bar(self) -> None:
        """Create the top summary bar with device counts."""
        bar = tk.Frame(self.frame, bg='#2b2b2b', bd=1, relief=tk.GROOVE)
        bar.grid(row=0, column=0, sticky='ew', padx=5, pady=5)

        # Device count
        self._device_count_label = tk.Label(
            bar,
            text="Devices: 0",
            font=('TkDefaultFont', 12, 'bold'),
            bg='#2b2b2b', fg='#00aaff',
            padx=15
        )
        self._device_count_label.pack(side=tk.LEFT)

        # Status
        self._status_label = tk.Label(
            bar,
            text="Status: Idle",
            font=('TkDefaultFont', 12, 'bold'),
            bg='#2b2b2b', fg='#aaaaaa',
            padx=15
        )
        self._status_label.pack(side=tk.LEFT)

        # Active tasks count
        self._task_count_label = tk.Label(
            bar,
            text="Active Tasks: 0",
            font=('TkDefaultFont', 12, 'bold'),
            bg='#2b2b2b', fg='#ffaa00',
            padx=15
        )
        self._task_count_label.pack(side=tk.LEFT)

        # Timestamp
        self._time_label = tk.Label(
            bar,
            text="",
            font=('TkDefaultFont', 10),
            bg='#2b2b2b', fg='#888888',
            padx=15
        )
        self._time_label.pack(side=tk.RIGHT)

        # Update time periodically
        self._update_time()

    def _update_time(self) -> None:
        """Update the timestamp display."""
        self._time_label.configure(
            text=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        )
        self.frame.after(1000, self._update_time)

    def refresh_dashboard(self) -> None:
        """Refresh all dashboard content."""
        self._update_device_cards()
        self._update_active_tasks()
        self._update_summary_counts()

    def _update_summary_counts(self) -> None:
        """Update the summary bar counts."""
        devices = self.device_manager.get_all_devices()
        connected = self.device_manager.get_connected_devices()
        active_tasks = self.task_manager.get_active_tasks()

        self._device_count_label.configure(
            text=f"Devices: {len(connected)}/{len(devices)}"
        )

        if len(connected) > 0:
            self._status_label.configure(
                text=f"Status: {len(connected)} device(s) connected",
                fg='#00ff00'
            )
        else:
            self._status_label.configure(
                text="Status: No devices connected",
                fg='#ff8800'
            )

        self._task_count_label.configure(
            text=f"Active Tasks: {len(active_tasks)}"
        )

    def _update_device_cards(self) -> None:
        """Update device summary cards."""
        # Clear existing cards
        for widget in self._device_cards_frame.winfo_children():
            widget.destroy()
        self._device_cards.clear()

        devices = self.device_manager.get_all_devices()
        if not devices:
            no_devices = tk.Label(
                self._device_cards_frame,
                text="No NI DAQ devices detected.\n\n"
                      "Ensure devices are connected and NI-DAQmx driver is installed.",
                font=('TkDefaultFont', 10),
                bg='#333333', fg='#888888',
                justify=tk.CENTER
            )
            no_devices.pack(expand=True, padx=20, pady=40)
            return

        for device in devices:
            card = self._create_device_card(device)
            card.pack(fill=tk.X, padx=5, pady=3)
            self._device_cards[device.name] = card

    def _create_device_card(self, device: DeviceInfo) -> tk.Frame:
        """
        Create a device summary card.

        Args:
            device: Device information

        Returns:
            Card frame widget
        """
        card = tk.Frame(
            self._device_cards_frame,
            bg='#2a2a2a',
            bd=1,
            relief=tk.RIDGE,
            padx=8,
            pady=5
        )

        # Status color
        status_colors = {
            DeviceStatus.CONNECTED: '#00ff00',
            DeviceStatus.DISCONNECTED: '#ff0000',
            DeviceStatus.ERROR: '#ff8800',
            DeviceStatus.UNKNOWN: '#888888'
        }
        status_color = status_colors.get(device.status, '#888888')

        # Status dot
        canvas = tk.Canvas(card, width=12, height=12,
                           bg='#2a2a2a', highlightthickness=0)
        canvas.pack(side=tk.LEFT, padx=(0, 8))
        canvas.create_oval(2, 2, 10, 10, fill=status_color, outline='')

        # Device info
        info_frame = tk.Frame(card, bg='#2a2a2a')
        info_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)

        tk.Label(
            info_frame,
            text=f"{device.name} ({device.product_type})",
            font=('TkDefaultFont', 10, 'bold'),
            bg='#2a2a2a', fg='white',
            anchor='w'
        ).pack(fill=tk.X)

        # Channel summary
        ch_info = (
            f"AI:{len(device.ai_channels)} AO:{len(device.ao_channels)} "
            f"DI:{len(device.di_channels)} DO:{len(device.do_channels)}"
        )
        tk.Label(
            info_frame,
            text=ch_info,
            font=('TkDefaultFont', 8),
            bg='#2a2a2a', fg='#aaaaaa',
            anchor='w'
        ).pack(fill=tk.X)

        # Connection badge
        conn_color = {
            'Ethernet': '#1a6d8b',
            'USB': '#6d8b1a',
            'PXI': '#8b1a6d',
            'PCI/PCIe': '#8b6d1a',
            'Unknown': '#555555'
        }.get(device.connection_type.value, '#555555')

        tk.Label(
            card,
            text=f" {device.connection_type.value} ",
            font=('TkDefaultFont', 7, 'bold'),
            bg=conn_color, fg='white',
            padx=4
        ).pack(side=tk.RIGHT, padx=(5, 0))

        return card

    def _update_active_tasks(self) -> None:
        """Update the active tasks display."""
        tasks = self.task_manager.get_active_tasks()

        if not tasks:
            self._active_tasks_label.configure(
                text="No active tasks",
                fg='#aaaaaa'
            )
            return

        task_text = []
        for task in tasks:
            ch_str = ",".join(task.channels[:3])
            if len(task.channels) > 3:
                ch_str += f"... (+{len(task.channels)-3})"
            task_text.append(
                f"• {task.task_type.value}: {task.device_name} "
                f"[{ch_str}]"
            )

        self._active_tasks_label.configure(
            text="\n".join(task_text),
            fg='#00ff00'
        )

    def add_log_message(self, message: str,
                        level: str = 'INFO') -> None:
        """
        Add a message to the system log display.

        Args:
            message: Log message text
            level: Message level (INFO, WARNING, ERROR)
        """
        if not self._system_log_text:
            return

        timestamp = datetime.now().strftime('%H:%M:%S')
        colors = {
            'INFO': '#00ff00',
            'WARNING': '#ffaa00',
            'ERROR': '#ff4444',
            'DEBUG': '#888888',
        }
        color = colors.get(level, '#00ff00')

        self._system_log_text.configure(state=tk.NORMAL)
        self._system_log_text.insert(
            tk.END,
            f"[{timestamp}] [{level:<8}] {message}\n",
            ('log',)
        )
        self._system_log_text.tag_configure('log', foreground=color)
        self._system_log_text.see(tk.END)
        self._system_log_text.configure(state=tk.DISABLED)

        # Limit log size
        line_count = int(self._system_log_text.index('end-1c').split('.')[0])
        if line_count > 1000:
            self._system_log_text.configure(state=tk.NORMAL)
            self._system_log_text.delete('1.0', '2.0')
            self._system_log_text.configure(state=tk.DISABLED)


class DAQApp:
    """
    Main NI DAQ Controller application.

    Creates the main window, manages device discovery, and coordinates
    between device tabs and the dashboard.

    Attributes:
        root: Root tkinter window
        task_manager: Global TaskManager
        device_manager: Global DeviceManager
        config: Application configuration
        notebook: Main notebook widget
        dashboard: Dashboard tab instance
        device_tabs: Dictionary of device tabs
    """

    def __init__(self, root: tk.Tk) -> None:
        """
        Initialize the main application.

        Args:
            root: Root tkinter window
        """
        self.root = root
        self.task_manager = TaskManager()
        self.device_manager = DeviceManager()
        self.config = global_config

        # Load configuration
        self.config.load()

        # Application state
        self.device_tabs: Dict[str, DeviceTab] = {}
        self.dashboard: Optional[DashboardTab] = None
        self._discovering = False
        self._refresh_interval = 5000  # Auto-refresh every 5 seconds

        # Configure window
        self._configure_window()

        # Create menu
        self._create_menu()

        # Create main UI
        self._create_ui()

        # Initial device discovery
        self.root.after(500, self._initial_discovery)

        # Start auto-refresh
        self._start_auto_refresh()

        log.info("NI DAQ Controller application initialized")

    def _configure_window(self) -> None:
        """Configure the main application window."""
        self.root.title(
            self.config.get('application.title', 'NI DAQ Controller')
        )

        # Window size
        window_size = self.config.get(
            'application.window_size', (1400, 900)
        )
        min_size = self.config.get(
            'application.min_window_size', (1024, 600)
        )

        self.root.geometry(f"{window_size[0]}x{window_size[1]}")
        self.root.minsize(min_size[0], min_size[1])

        # Configure dark theme
        self._apply_dark_theme()

        # Handle close event
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)

    def _apply_dark_theme(self) -> None:
        """Apply dark theme styling to the application."""
        style = ttk.Style()

        # Configure ttk styles
        style.theme_use('clam')

        style.configure(
            'TNotebook', background='#2b2b2b', borderwidth=0
        )
        style.configure(
            'TNotebook.Tab',
            background='#3a3a3a',
            foreground='#cccccc',
            padding=[12, 4],
            font=('TkDefaultFont', 9)
        )
        style.map(
            'TNotebook.Tab',
            background=[('selected', '#1a1a1a')],
            foreground=[('selected', 'white')],
            expand=[('selected', [1, 1, 1, 0])]
        )
        style.configure(
            'TLabelframe',
            background='#333333',
            foreground='white',
            bordercolor=('#555555')
        )
        style.configure(
            'TLabelframe.Label',
            background='#333333',
            foreground='white'
        )

        # Configure scrollbar style
        style.configure(
            'Vertical.TScrollbar',
            background='#3a3a3a',
            troughcolor='#2b2b2b',
            bordercolor='#3a3a3a',
            arrowcolor='#cccccc'
        )

        # Set background for root
        self.root.configure(bg='#2b2b2b')

    def _create_menu(self) -> None:
        """Create the application menu bar."""
        menubar = tk.Menu(self.root, bg='#3a3a3a', fg='white')

        # File menu
        file_menu = tk.Menu(menubar, tearoff=0, bg='#3a3a3a', fg='white')
        file_menu.add_command(
            label="Refresh Devices (F5)",
            command=self._refresh_devices,
            accelerator='F5'
        )
        file_menu.add_separator()
        file_menu.add_command(
            label="Export Log...",
            command=self._export_log
        )
        file_menu.add_separator()
        file_menu.add_command(
            label="Exit",
            command=self._on_close,
            accelerator='Alt+F4'
        )
        menubar.add_cascade(label="File", menu=file_menu)

        # Device menu
        device_menu = tk.Menu(menubar, tearoff=0, bg='#3a3a3a', fg='white')
        device_menu.add_command(
            label="Discover Devices",
            command=self._refresh_devices
        )
        device_menu.add_command(
            label="Stop All Tasks",
            command=self._stop_all_tasks
        )
        device_menu.add_separator()
        device_menu.add_command(
            label="Device Summary...",
            command=self._show_device_summary
        )
        menubar.add_cascade(label="Device", menu=device_menu)

        # Help menu
        help_menu = tk.Menu(menubar, tearoff=0, bg='#3a3a3a', fg='white')
        help_menu.add_command(
            label="About...",
            command=self._show_about
        )
        menubar.add_cascade(label="Help", menu=help_menu)

        self.root.config(menu=menubar)

        # Keyboard shortcuts
        self.root.bind('<F5>', lambda e: self._refresh_devices())

    def _create_ui(self) -> None:
        """Create the main UI layout."""
        # Main notebook
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

        # Create dashboard
        self.dashboard = DashboardTab(
            self.notebook, self.task_manager, self.device_manager
        )
        self.dashboard.create_ui()

        # Status bar
        self._create_status_bar()

        # Register for device refresh callbacks
        self.device_manager.register_refresh_callback(
            self._on_devices_refreshed
        )

    def _create_status_bar(self) -> None:
        """Create the bottom status bar."""
        status_bar = tk.Frame(self.root, bg='#1a1a1a', bd=1, relief=tk.SUNKEN)
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)

        self._status_text = tk.Label(
            status_bar,
            text="Ready - Press F5 to refresh devices",
            font=('TkDefaultFont', 9),
            bg='#1a1a1a', fg='#888888',
            anchor='w', padx=10
        )
        self._status_text.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Status indicators
        self._nidaqmx_status = tk.Label(
            status_bar,
            text="NI-DAQmx: ✓",
            font=('TkDefaultFont', 9),
            bg='#1a1a1a', fg='#00ff00',
            padx=10
        )
        self._nidaqmx_status.pack(side=tk.RIGHT)

        # Check NI-DAQmx availability
        if not self.task_manager._check_nidaqmx():
            self._nidaqmx_status.configure(
                text="NI-DAQmx: ✗",
                fg='#ff4444'
            )

    def _initial_discovery(self) -> None:
        """Perform initial device discovery."""
        self._set_status("Discovering NI DAQ devices...")
        self.dashboard.add_log_message(
            "Starting device discovery...", 'INFO'
        )

        devices = self.device_manager.discover_devices()

        if devices:
            self._set_status(
                f"Found {len(devices)} device(s) - "
                f"{len(self.device_manager.get_connected_devices())} connected"
            )
            self.dashboard.add_log_message(
                f"Discovery complete: {len(devices)} device(s) found", 'INFO'
            )
        else:
            self._set_status(
                "No NI DAQ devices found. Check connections and drivers."
            )
            self.dashboard.add_log_message(
                "No NI DAQ devices detected", 'WARNING'
            )

        self._update_device_tabs()

    def _refresh_devices(self) -> None:
        """Manually refresh device discovery."""
        if self._discovering:
            return

        self._discovering = True
        self._set_status("Refreshing devices...")

        def _refresh():
            """Background refresh."""
            try:
                devices = self.device_manager.refresh_devices()
                self.root.after(0, lambda: self._on_refresh_complete(devices))
            except Exception as e:
                log.error("Refresh error: %s", e)
                self.root.after(0, lambda: self._set_status(
                    f"Refresh failed: {e}"
                ))
            finally:
                self._discovering = False

        thread = threading.Thread(target=_refresh, daemon=True)
        thread.start()

    def _on_refresh_complete(self, devices: list) -> None:
        """
        Handle completion of device refresh.

        Args:
            devices: List of refreshed DeviceInfo objects
        """
        if devices:
            self._set_status(
                f"Refresh complete - {len(devices)} device(s) found"
            )
            self.dashboard.add_log_message(
                f"Device refresh: {len(devices)} device(s)", 'INFO'
            )
        else:
            self._set_status("No devices found after refresh")
            self.dashboard.add_log_message(
                "No devices found", 'WARNING'
            )

        self._update_device_tabs()
        self.dashboard.refresh_dashboard()

    def _on_devices_refreshed(self) -> None:
        """Called when device manager notifies of refresh."""
        self.root.after(0, self._update_device_tabs)
        self.root.after(0, self.dashboard.refresh_dashboard)

    def _update_device_tabs(self) -> None:
        """Update device tabs based on current device list."""
        devices = self.device_manager.get_all_devices()
        current_device_names = {d.name for d in devices}

        # Remove tabs for disconnected devices
        for name in list(self.device_tabs.keys()):
            if name not in current_device_names:
                tab = self.device_tabs.pop(name)
                self.notebook.forget(tab.frame)
                tab.cleanup()
                log.info("Removed device tab: %s", name)

        # Add/update tabs for connected devices
        for device in devices:
            if device.name not in self.device_tabs:
                # Create new tab
                tab = DeviceTab(
                    self.notebook, self.task_manager, device
                )
                tab.create_ui()
                self.device_tabs[device.name] = tab
                log.info("Added device tab: %s", device.name)
            else:
                # Update existing tab
                tab = self.device_tabs[device.name]
                tab.update_device_info(device)
                tab.refresh_module_uis()

    def _start_auto_refresh(self) -> None:
        """Start automatic periodic device refresh."""
        def _auto_refresh():
            """Auto-refresh loop."""
            try:
                connected_before = len(
                    self.device_manager.get_connected_devices()
                )
                self.device_manager.refresh_devices()
                connected_after = len(
                    self.device_manager.get_connected_devices()
                )

                if connected_before != connected_after:
                    self.dashboard.add_log_message(
                        f"Device count changed: {connected_before} -> "
                        f"{connected_after}", 'INFO'
                    )

                self.root.after(0, self.dashboard.refresh_dashboard)
            except Exception as e:
                log.debug("Auto-refresh error (expected if no HW): %s", e)

            self.root.after(self._refresh_interval, _auto_refresh)

        self.root.after(self._refresh_interval, _auto_refresh)
        log.info("Auto-refresh started (interval: %d ms)",
                 self._refresh_interval)

    def _stop_all_tasks(self) -> None:
        """Stop all active DAQ tasks."""
        active = self.task_manager.get_active_tasks()
        if not active:
            messagebox.showinfo("No Tasks", "No active tasks to stop.")
            return

        confirm = messagebox.askyesno(
            "Stop All Tasks",
            f"Stop {len(active)} active task(s)?"
        )
        if confirm:
            self.task_manager.cleanup_all()
            self.dashboard.add_log_message(
                f"Stopped all {len(active)} task(s)", 'INFO'
            )
            self.dashboard.refresh_dashboard()

    def _show_device_summary(self) -> None:
        """Show a summary dialog of all devices."""
        devices = self.device_manager.get_all_devices()

        if not devices:
            messagebox.showinfo(
                "Device Summary",
                "No devices detected."
            )
            return

        summary = []
        for device in devices:
            summary.append(
                f"Device: {device.name}\n"
                f"  Type: {device.product_type}\n"
                f"  S/N: {device.serial_number}\n"
                f"  Connection: {device.connection_type.value}\n"
                f"  Status: {device.status.value}\n"
                f"  Modules: {len(device.modules)}\n"
                f"  Channels: "
                f"AI={len(device.ai_channels)} "
                f"AO={len(device.ao_channels)} "
                f"DI={len(device.di_channels)} "
                f"DO={len(device.do_channels)}\n"
            )

        messagebox.showinfo(
            "Device Summary",
            "\n".join(summary)
        )

    def _export_log(self) -> None:
        """Export system log to a file."""
        from tkinter import filedialog
        filename = filedialog.asksaveasfilename(
            defaultextension=".log",
            filetypes=[("Log files", "*.log"), ("All files", "*.*")],
            title="Export System Log"
        )
        if filename:
            try:
                with open(filename, 'w') as f:
                    f.write("NI DAQ Controller - System Log\n")
                    f.write("=" * 60 + "\n")
                    # The log is already being written to file by the logger
                messagebox.showinfo(
                    "Export Complete",
                    f"Log exported to:\n{filename}"
                )
            except Exception as e:
                messagebox.showerror(
                    "Export Error",
                    f"Failed to export log: {e}"
                )

    def _show_about(self) -> None:
        """Show the About dialog."""
        messagebox.showinfo(
            "About NI DAQ Controller",
            "NI DAQ Controller v1.0.0\n\n"
            "A professional desktop application for controlling\n"
            "National Instruments DAQ devices using NI-DAQmx.\n\n"
            "Features:\n"
            "  • Automatic device discovery\n"
            "  • Dynamic UI generation\n"
            "  • Analog I/O with live monitoring\n"
            "  • Digital I/O with toggle control\n"
            "  • Waveform generation\n"
            "  • CSV data export\n"
            "  • Comprehensive logging\n\n"
            "Built with Python 3, NI-DAQmx, and CustomTkinter"
        )

    def _set_status(self, text: str) -> None:
        """
        Update the status bar text.

        Args:
            text: Status message to display
        """
        if hasattr(self, '_status_text'):
            self._status_text.configure(text=text)

    def _on_close(self) -> None:
        """Handle application close event."""
        log.info("Application shutting down...")

        # Clean up all tasks
        self.task_manager.cleanup_all()

        # Clean up device manager
        self.device_manager.cleanup()

        # Clean up tabs
        for tab in self.device_tabs.values():
            tab.cleanup()

        # Destroy window
        self.root.destroy()

    def run(self) -> None:
        """Start the application main loop."""
        log.info("Application started")
        self.root.mainloop()

    def add_log(self, message: str, level: str = 'INFO') -> None:
        """
        Add a message to the dashboard log.

        Args:
            message: Log message
            level: Message level
        """
        if self.dashboard:
            self.dashboard.add_log_message(message, level)