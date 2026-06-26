"""
Device Tab module for NI DAQ Controller.

Creates individual tabs for each discovered NI DAQ device. Each tab displays
device information and dynamically generates module UI sections based on
the device's installed modules and capabilities.

Features:
    - Device summary header with connection info
    - Dynamic module UI sections
    - Scrollable content for multiple modules
    - Status indicators for each module
    - Device-level controls (refresh, disconnect detection)

Typical usage:
    from device_tab import DeviceTab
    tab = DeviceTab(notebook, task_manager, device_info)
    tab.create_ui()
"""

import tkinter as tk
from tkinter import ttk
from typing import Optional, Dict, Any
from logger import get_logger
from task_manager import TaskManager
from device_manager import DeviceInfo, DeviceStatus, ModuleInfo
from module_manager import ModuleUIFactory

log = get_logger(__name__)


class DeviceTab:
    """
    A tab representing a single NI DAQ device.

    Displays device information and dynamically creates UI sections
    for each installed module based on the device's configuration.

    Attributes:
        notebook: Parent notebook widget
        task_manager: Global TaskManager instance
        device_info: Information about this device
        frame: Main frame for this tab
        _module_factory: Factory for module UI creation
        _status_label: Label showing device status
    """

    def __init__(self, notebook: ttk.Notebook,
                 task_manager: TaskManager,
                 device_info: DeviceInfo) -> None:
        """
        Initialize the device tab.

        Args:
            notebook: Parent notebook widget
            task_manager: Global TaskManager instance
            device_info: Information about this device
        """
        self.notebook = notebook
        self.task_manager = task_manager
        self.device_info = device_info
        self.device_name = device_info.name

        # Create main frame
        self.frame = ttk.Frame(notebook)
        self._module_factory: Optional[ModuleUIFactory] = None
        self._status_label: Optional[tk.Label] = None
        self._device_header: Optional[tk.Frame] = None

        log.info("DeviceTab created for: %s", self.device_name)

    def create_ui(self) -> None:
        """
        Create the complete UI for this device tab.

        Creates the device header, scrollable content area, and
        dynamically generates module UI sections.
        """
        # Add tab to notebook
        self.notebook.add(self.frame, text=self._get_tab_title())

        # Configure grid
        self.frame.grid_rowconfigure(1, weight=1)
        self.frame.grid_columnconfigure(0, weight=1)

        # Create device header
        self._create_device_header()

        # Create scrollable content area for modules
        self._create_module_area()

        # Generate module UIs
        self._create_module_uis()

        log.info("UI created for device: %s", self.device_name)

    def _get_tab_title(self) -> str:
        """
        Get the title for this device's tab.

        Returns:
            Short display name for the tab
        """
        # Use short name if available
        name = self.device_name

        # Truncate if too long
        if len(name) > 20:
            # Try to use a shorter form
            parts = name.split('/')
            if parts:
                name = parts[0]

        return name

    def _create_device_header(self) -> None:
        """
        Create the device information header at the top of the tab.
        """
        info = self.device_info

        header_frame = tk.Frame(
            self.frame,
            bg='#2b2b2b',
            bd=2,
            relief=tk.GROOVE,
            padx=10,
            pady=8
        )
        header_frame.grid(row=0, column=0, sticky='ew', padx=5, pady=5)

        # Device name and status
        title_frame = tk.Frame(header_frame, bg='#2b2b2b')
        title_frame.pack(fill=tk.X)

        # Status indicator
        status_color = {
            DeviceStatus.CONNECTED: '#00ff00',
            DeviceStatus.DISCONNECTED: '#ff0000',
            DeviceStatus.ERROR: '#ff8800',
            DeviceStatus.UNKNOWN: '#888888'
        }.get(info.status, '#888888')

        self._status_indicator = tk.Canvas(
            title_frame, width=16, height=16,
            bg='#2b2b2b', highlightthickness=0
        )
        self._status_indicator.pack(side=tk.LEFT, padx=(0, 8))
        self._status_indicator.create_oval(2, 2, 14, 14, fill=status_color, outline='')

        # Device name
        tk.Label(
            title_frame,
            text=info.name,
            font=('TkDefaultFont', 14, 'bold'),
            bg='#2b2b2b',
            fg='white'
        ).pack(side=tk.LEFT)

        # Product type
        tk.Label(
            title_frame,
            text=f"  ({info.product_type})",
            font=('TkDefaultFont', 11),
            bg='#2b2b2b',
            fg='#aaaaaa'
        ).pack(side=tk.LEFT)

        # Connection type badge
        conn_colors = {
            'Ethernet': '#1a6d8b',
            'USB': '#6d8b1a',
            'PXI': '#8b1a6d',
            'PCI/PCIe': '#8b6d1a',
            'Unknown': '#555555'
        }
        conn_color = conn_colors.get(info.connection_type.value, '#555555')

        conn_badge = tk.Label(
            title_frame,
            text=f" {info.connection_type.value} ",
            font=('TkDefaultFont', 8, 'bold'),
            bg=conn_color,
            fg='white',
            padx=6
        )
        conn_badge.pack(side=tk.RIGHT, padx=2)

        # Device details
        details_frame = tk.Frame(header_frame, bg='#2b2b2b')
        details_frame.pack(fill=tk.X, pady=(5, 0))

        details = []

        if info.serial_number:
            details.append(f"S/N: {info.serial_number}")

        if info.ip_address:
            details.append(f"IP: {info.ip_address}")

        if info.is_simulated:
            details.append("SIMULATED")

        details.append(f"Modules: {len(info.modules)}")

        total_channels = (
            len(info.ai_channels) + len(info.ao_channels) +
            len(info.di_channels) + len(info.do_channels)
        )
        details.append(f"Channels: {total_channels}")

        tk.Label(
            details_frame,
            text=" | ".join(details),
            font=('TkDefaultFont', 9),
            bg='#2b2b2b',
            fg='#cccccc'
        ).pack(anchor='w')

        # Channel summary
        ch_frame = tk.Frame(header_frame, bg='#2b2b2b')
        ch_frame.pack(fill=tk.X, pady=(3, 0))

        ch_types = [
            ('AI', len(info.ai_channels), '#00cc00'),
            ('AO', len(info.ao_channels), '#ff8800'),
            ('DI', len(info.di_channels), '#00aaff'),
            ('DO', len(info.do_channels), '#ff66cc'),
        ]

        for label, count, color in ch_types:
            if count > 0:
                badge = tk.Label(
                    ch_frame,
                    text=f" {label}: {count} ",
                    font=('TkDefaultFont', 8),
                    bg=color,
                    fg='black',
                    padx=4
                )
                badge.pack(side=tk.LEFT, padx=(0, 4))

        self._device_header = header_frame

    def _create_module_area(self) -> None:
        """
        Create scrollable canvas area for module UI sections.
        """
        # Create canvas with scrollbar for module content
        canvas_frame = tk.Frame(self.frame, bg='#333333')
        canvas_frame.grid(row=1, column=0, sticky='nsew', padx=5, pady=5)
        canvas_frame.grid_rowconfigure(0, weight=1)
        canvas_frame.grid_columnconfigure(0, weight=1)

        self._canvas = tk.Canvas(
            canvas_frame,
            bg='#333333',
            highlightthickness=0
        )
        self._canvas.grid(row=0, column=0, sticky='nsew')

        scrollbar = ttk.Scrollbar(
            canvas_frame,
            orient=tk.VERTICAL,
            command=self._canvas.yview
        )
        scrollbar.grid(row=0, column=1, sticky='ns')

        self._canvas.configure(yscrollcommand=scrollbar.set)

        # Inner frame for module content
        self._module_container = tk.Frame(self._canvas, bg='#333333')
        self._canvas_window = self._canvas.create_window(
            (0, 0),
            window=self._module_container,
            anchor='nw',
            width=self._canvas.winfo_width()
        )

        # Configure scrolling
        self._module_container.bind(
            '<Configure>',
            lambda e: self._canvas.configure(
                scrollregion=self._canvas.bbox('all')
            )
        )
        self._canvas.bind(
            '<Configure>',
            lambda e: self._canvas.itemconfig(
                self._canvas_window,
                width=e.width
            )
        )

        # Mouse wheel scrolling
        def _on_mousewheel(event) -> None:
            """Handle mouse wheel scrolling."""
            self._canvas.yview_scroll(
                int(-1 * (event.delta / 120)),
                'units'
            )

        self._canvas.bind_all('<MouseWheel>', _on_mousewheel)

        # Create module factory
        self._module_factory = ModuleUIFactory(
            self.task_manager,
            self._module_container
        )

    def _create_module_uis(self) -> None:
        """
        Create UI sections for each detected module.
        """
        if not self._module_factory:
            log.error("Module factory not initialized")
            return

        for module_info in self.device_info.modules:
            try:
                self._module_factory.create_module_ui(module_info)
            except Exception as e:
                log.error(
                    "Failed to create UI for module %s: %s",
                    module_info.name, e
                )

    def update_device_info(self, device_info: DeviceInfo) -> None:
        """
        Update the device tab with new device information.

        Called when device list is refreshed.

        Args:
            device_info: Updated device information
        """
        self.device_info = device_info

        # Update status indicator
        status_color = {
            DeviceStatus.CONNECTED: '#00ff00',
            DeviceStatus.DISCONNECTED: '#ff0000',
            DeviceStatus.ERROR: '#ff8800',
        }.get(device_info.status, '#888888')

        if hasattr(self, '_status_indicator'):
            self._status_indicator.delete('all')
            self._status_indicator.create_oval(
                2, 2, 14, 14, fill=status_color, outline=''
            )

        log.debug("Updated device info for: %s (status: %s)",
                  device_info.name, device_info.status.value)

    def refresh_module_uis(self) -> None:
        """
        Recreate module UI sections (call after device refresh).
        """
        # Clear existing module UI
        for widget in self._module_container.winfo_children():
            widget.destroy()

        # Recreate module UIs
        self._create_module_uis()

        log.info("Module UIs refreshed for: %s", self.device_name)

    def get_module_factory(self) -> Optional[ModuleUIFactory]:
        """
        Get the module UI factory for this device tab.

        Returns:
            ModuleUIFactory instance if available
        """
        return self._module_factory

    def cleanup(self) -> None:
        """
        Clean up resources used by this device tab.
        """
        if self._module_factory:
            self._module_factory.cleanup()

        # Remove mouse wheel binding
        try:
            self._canvas.unbind_all('<MouseWheel>')
        except Exception:
            pass

        log.info("DeviceTab cleaned up: %s", self.device_name)