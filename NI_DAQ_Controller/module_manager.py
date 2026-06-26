"""
Module Manager for NI DAQ Controller.

Creates dynamic UI controls for each detected module based on its
capabilities. Handles the creation of collapsible sections for AI, AO,
DI, DO, and counter/timer operations within each device tab.

Each module section provides:
    - Read Mode / Write Mode selection
    - Continuous Read / Continuous Write
    - Stop Operation
    - Channel-specific controls
    - Live value display
    - Status indicators

Typical usage:
    from module_manager import ModuleUIFactory
    factory = ModuleUIFactory(task_manager, parent_frame)
    factory.create_module_ui(module_info)
"""

import tkinter as tk
from tkinter import ttk, messagebox
from typing import List, Optional, Dict, Any, Callable, Tuple
import numpy as np
from logger import get_logger
from task_manager import TaskManager
from device_manager import ModuleInfo, DeviceInfo
from analog_input import AnalogInputController, AcquisitionResult
from analog_output import AnalogOutputController, OutputConfig, \
    SignalType, WaveformType
from digital_io import DigitalIOController, DigitalChannelInfo, CounterMode

log = get_logger(__name__)


class CollapsibleFrame(tk.Frame):
    """
    A collapsible frame section for module controls.

    Provides a header that can be clicked to expand/collapse the
    content section below it.

    Attributes:
        header: Header frame
        content: Content frame (shown/hidden)
        _expanded: Whether the section is expanded
    """

    def __init__(self, parent: tk.Widget, title: str,
                 expanded: bool = True, **kwargs) -> None:
        """
        Initialize collapsible frame.

        Args:
            parent: Parent widget
            title: Section title text
            expanded: Whether to start expanded
            **kwargs: Additional frame options
        """
        super().__init__(parent, **kwargs)

        self._expanded = expanded
        self._title = title

        # Configure style
        self.configure(bd=1, relief=tk.GROOVE, padx=5, pady=2)

        # Header
        self.header = tk.Frame(self, bg='#2b2b2b', cursor='hand2')
        self.header.pack(fill=tk.X, padx=2, pady=2)

        self.toggle_btn = tk.Label(
            self.header,
            text='▼' if expanded else '▶',
            font=('TkDefaultFont', 10, 'bold'),
            bg='#2b2b2b',
            fg='white'
        )
        self.toggle_btn.pack(side=tk.LEFT, padx=(5, 2))

        self.title_label = tk.Label(
            self.header,
            text=title,
            font=('TkDefaultFont', 11, 'bold'),
            bg='#2b2b2b',
            fg='white',
            anchor='w'
        )
        self.title_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        # Bind click events
        for widget in (self.header, self.toggle_btn, self.title_label):
            widget.bind('<Button-1>', self._toggle)

        # Content frame
        self.content = tk.Frame(self, bg='#333333')
        self.content.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        if not expanded:
            self.content.pack_forget()

    def _toggle(self, event=None) -> None:
        """Toggle expanded/collapsed state."""
        self._expanded = not self._expanded

        if self._expanded:
            self.content.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
            self.toggle_btn.configure(text='▼')
        else:
            self.content.pack_forget()
            self.toggle_btn.configure(text='▶')

    def add_widget(self, widget: tk.Widget) -> None:
        """
        Add a widget to the content frame.

        Args:
            widget: Widget to add
        """
        widget.pack(in_=self.content, fill=tk.X, padx=2, pady=1)

    @property
    def expanded(self) -> bool:
        """Whether the section is currently expanded."""
        return self._expanded


class ModuleUIFactory:
    """
    Factory class for creating module-specific UI controls.

    Automatically generates UI sections based on module capabilities,
    creating appropriate controls for analog I/O, digital I/O,
    and counter/timer operations.

    Attributes:
        task_manager: Global TaskManager instance
        parent: Parent widget for UI creation
        _controllers: Active controllers for each module
        _plots: Active plot widgets for monitoring
    """

    def __init__(self, task_manager: TaskManager,
                 parent: tk.Widget) -> None:
        """
        Initialize module UI factory.

        Args:
            task_manager: Global TaskManager instance
            parent: Parent widget to place module UIs in
        """
        self.task_manager = task_manager
        self.parent = parent

        self._analog_controllers: Dict[str, AnalogInputController] = {}
        self._output_controllers: Dict[str, AnalogOutputController] = {}
        self._digital_controllers: Dict[str, DigitalIOController] = {}
        self._sections: Dict[str, CollapsibleFrame] = {}
        self._value_labels: Dict[str, tk.Label] = {}
        self._continuous_tasks: Dict[str, str] = {}

        log.info("ModuleUIFactory initialized")

    def create_module_ui(self, module_info: ModuleInfo) -> None:
        """
        Create UI controls for a module based on its capabilities.

        Args:
            module_info: Module information from device discovery
        """
        module_name = module_info.name
        ops = module_info.supported_operations

        log.info("Creating UI for module: %s (ops: %s)", module_name, ops)

        # Create collapsible section
        section = CollapsibleFrame(
            self.parent,
            title=self._format_module_title(module_info),
            expanded=True
        )
        section.pack(fill=tk.X, padx=5, pady=5, anchor='n')
        self._sections[module_name] = section

        # Module info header
        info_frame = tk.Frame(section.content, bg='#333333')
        info_frame.pack(fill=tk.X, padx=5, pady=2)

        info_text = (
            f"Type: {module_info.product_type} | "
            f"Slot: {module_info.slot_number} | "
            f"S/N: {module_info.serial_number}"
        )
        tk.Label(
            info_frame,
            text=info_text,
            font=('TkDefaultFont', 8),
            bg='#333333',
            fg='#aaaaaa'
        ).pack(anchor='w')

        # Create operation-specific controls
        if "AI" in ops:
            self._create_analog_input_ui(module_info, section)

        if "AO" in ops:
            self._create_analog_output_ui(module_info, section)

        if "DI" in ops or "DO" in ops:
            self._create_digital_io_ui(module_info, section)

        if "CI" in ops or "CO" in ops:
            self._create_counter_ui(module_info, section)

    def _format_module_title(self, module_info: ModuleInfo) -> str:
        """
        Format the module section title.

        Args:
            module_info: Module information

        Returns:
            Formatted title string
        """
        ops_str = ", ".join(module_info.supported_operations)
        channels = (
            f"AI:{len(module_info.ai_channels)} "
            f"AO:{len(module_info.ao_channels)} "
            f"DI:{len(module_info.di_channels)} "
            f"DO:{len(module_info.do_channels)}"
        )
        return f"{module_info.name} [{ops_str}] ({channels})"

    def _create_analog_input_ui(self,
                                 module_info: ModuleInfo,
                                 section: CollapsibleFrame) -> None:
        """
        Create analog input UI controls.

        Args:
            module_info: Module information
            section: Parent collapsible section
        """
        controller = AnalogInputController(
            self.task_manager, module_info
        )
        self._analog_controllers[module_info.name] = controller

        content = section.content
        ai_frame = tk.LabelFrame(
            content, text="Analog Input",
            font=('TkDefaultFont', 9, 'bold'),
            bg='#333333', fg='#00cc00',
            padx=10, pady=5
        )
        ai_frame.pack(fill=tk.X, padx=5, pady=5)

        # Channel selection
        ch_frame = tk.Frame(ai_frame, bg='#333333')
        ch_frame.pack(fill=tk.X, pady=2)

        tk.Label(
            ch_frame, text="Channels:", bg='#333333', fg='white'
        ).pack(side=tk.LEFT, padx=5)

        channels = controller.get_available_channels()
        channel_var = tk.StringVar(value=",".join(channels[:4] if len(channels) > 4 else channels))
        ch_entry = tk.Entry(
            ch_frame, textvariable=channel_var, width=30
        )
        ch_entry.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)

        # Sample rate
        sr_frame = tk.Frame(ai_frame, bg='#333333')
        sr_frame.pack(fill=tk.X, pady=2)

        tk.Label(
            sr_frame, text="Sample Rate (Hz):", bg='#333333', fg='white'
        ).pack(side=tk.LEFT, padx=5)

        max_rate = controller.get_max_sample_rate()
        rate_var = tk.StringVar(value="1000")
        rate_entry = tk.Entry(sr_frame, textvariable=rate_var, width=15)
        rate_entry.pack(side=tk.LEFT, padx=5)

        tk.Label(
            sr_frame, text=f"(Max: {max_rate:.0f})",
            bg='#333333', fg='#888888', font=('TkDefaultFont', 8)
        ).pack(side=tk.LEFT, padx=2)

        # Samples
        ns_frame = tk.Frame(ai_frame, bg='#333333')
        ns_frame.pack(fill=tk.X, pady=2)

        tk.Label(
            ns_frame, text="Samples:", bg='#333333', fg='white'
        ).pack(side=tk.LEFT, padx=5)

        samples_var = tk.StringVar(value="100")
        samples_entry = tk.Entry(ns_frame, textvariable=samples_var, width=10)
        samples_entry.pack(side=tk.LEFT, padx=5)

        # Voltage range
        vr_frame = tk.Frame(ai_frame, bg='#333333')
        vr_frame.pack(fill=tk.X, pady=2)

        tk.Label(
            vr_frame, text="Range:", bg='#333333', fg='white'
        ).pack(side=tk.LEFT, padx=5)

        ranges = controller.get_voltage_ranges()
        range_options = [f"{r[0]:.1f} to {r[1]:.1f} V" for r in ranges]
        range_var = tk.StringVar(value=range_options[0] if range_options else "-10.0 to 10.0 V")
        range_menu = ttk.Combobox(
            vr_frame, textvariable=range_var,
            values=range_options, state='readonly', width=20
        )
        range_menu.pack(side=tk.LEFT, padx=5)

        # Terminal configuration
        tc_frame = tk.Frame(ai_frame, bg='#333333')
        tc_frame.pack(fill=tk.X, pady=2)

        tk.Label(
            tc_frame, text="Terminal:", bg='#333333', fg='white'
        ).pack(side=tk.LEFT, padx=5)

        term_var = tk.StringVar(value="RSE")
        term_menu = ttk.Combobox(
            tc_frame, textvariable=term_var,
            values=["RSE", "NRSE", "Differential"],
            state='readonly', width=15
        )
        term_menu.pack(side=tk.LEFT, padx=5)

        # Live value display
        value_frame = tk.Frame(ai_frame, bg='#333333', bd=1, relief=tk.SUNKEN)
        value_frame.pack(fill=tk.X, pady=5)

        value_label = tk.Label(
            value_frame,
            text="-- V",
            font=('TkDefaultFont', 14, 'bold'),
            bg='#1a1a1a', fg='#00ff00',
            height=2
        )
        value_label.pack(fill=tk.X, padx=5, pady=2)
        self._value_labels[module_info.name + "_ai"] = value_label

        # Buttons
        btn_frame = tk.Frame(ai_frame, bg='#333333')
        btn_frame.pack(fill=tk.X, pady=5)

        def _read_single():
            """Read a single sample from selected channels."""
            try:
                ch_list = [c.strip() for c in channel_var.get().split(',') if c.strip()]
                rate = float(rate_var.get())
                samples = int(samples_var.get())
                range_str = range_var.get()
                range_parts = range_str.replace('V', '').split(' to ')
                vmin, vmax = float(range_parts[0]), float(range_parts[1])

                task_name = controller.start_single_acquisition(
                    ch_list, rate, samples, vmin, vmax, term_var.get()
                )
                if task_name:
                    self.task_manager.start_task(task_name)
                    result = controller.read_data(task_name)
                    if result and result.success:
                        values = []
                        for ch, data in result.data.items():
                            if len(data) > 0:
                                values.append(f"{ch}: {data[-1]:.4f} V")
                        if values:
                            value_label.configure(text=" | ".join(values))
                    self.task_manager.clear_task(task_name)
            except Exception as ex:
                log.error("Read error: %s", ex)
                messagebox.showerror("Read Error", str(ex))

        def _start_continuous():
            """Start continuous acquisition."""
            try:
                ch_list = [c.strip() for c in channel_var.get().split(',') if c.strip()]
                rate = float(rate_var.get())
                samples = int(samples_var.get())

                def _update_display(result: AcquisitionResult):
                    """Update display with new data."""
                    if result.success:
                        values = []
                        for ch, data in result.data.items():
                            if len(data) > 0:
                                values.append(f"{data[-1]:.4f}")
                        if values:
                            self.parent.after(0, lambda: value_label.configure(
                                text=" | ".join(
                                    f"{ch}: {v} V"
                                    for ch, v in zip(result.data.keys(), values)
                                )
                            ))

                task_name = controller.start_continuous_acquisition(
                    ch_list, rate, samples,
                    data_callback=_update_display
                )
                if task_name:
                    self._continuous_tasks[module_info.name] = task_name
                    start_btn.configure(state=tk.DISABLED)
                    stop_btn.configure(state=tk.NORMAL)
            except Exception as ex:
                log.error("Continuous start error: %s", ex)
                messagebox.showerror("Error", str(ex))

        def _stop_continuous():
            """Stop continuous acquisition."""
            task_name = self._continuous_tasks.pop(module_info.name, None)
            if task_name:
                controller.stop_acquisition(task_name)
                start_btn.configure(state=tk.NORMAL)
                stop_btn.configure(state=tk.DISABLED)
                value_label.configure(text="-- V")

        def _export_csv():
            """Export data to CSV."""
            task_name = self._continuous_tasks.get(module_info.name)
            if task_name:
                filepath = controller.export_data_to_csv(task_name)
                if filepath:
                    messagebox.showinfo("Export Complete",
                                        f"Data exported to:\n{filepath}")
                else:
                    messagebox.showwarning("Export Failed",
                                           "No data available to export")
            else:
                messagebox.showinfo("No Data",
                                    "No continuous acquisition active.\n"
                                    "Start acquisition first.")

        read_btn = tk.Button(
            btn_frame, text="Read Single",
            command=_read_single,
            bg='#2a6d2a', fg='white',
            width=12
        )
        read_btn.pack(side=tk.LEFT, padx=2)

        start_btn = tk.Button(
            btn_frame, text="▶ Continuous",
            command=_start_continuous,
            bg='#1a4a6d', fg='white',
            width=12
        )
        start_btn.pack(side=tk.LEFT, padx=2)

        stop_btn = tk.Button(
            btn_frame, text="■ Stop",
            command=_stop_continuous,
            bg='#8b0000', fg='white',
            width=10, state=tk.DISABLED
        )
        stop_btn.pack(side=tk.LEFT, padx=2)

        export_btn = tk.Button(
            btn_frame, text="Export CSV",
            command=_export_csv,
            bg='#4a4a4a', fg='white',
            width=10
        )
        export_btn.pack(side=tk.LEFT, padx=2)

    def _create_analog_output_ui(self,
                                  module_info: ModuleInfo,
                                  section: CollapsibleFrame) -> None:
        """
        Create analog output UI controls.

        Args:
            module_info: Module information
            section: Parent collapsible section
        """
        controller = AnalogOutputController(
            self.task_manager, module_info
        )
        self._output_controllers[module_info.name] = controller

        content = section.content
        ao_frame = tk.LabelFrame(
            content, text="Analog Output",
            font=('TkDefaultFont', 9, 'bold'),
            bg='#333333', fg='#ff8800',
            padx=10, pady=5
        )
        ao_frame.pack(fill=tk.X, padx=5, pady=5)

        # Channel selection
        ch_frame = tk.Frame(ao_frame, bg='#333333')
        ch_frame.pack(fill=tk.X, pady=2)

        tk.Label(
            ch_frame, text="Channel:", bg='#333333', fg='white'
        ).pack(side=tk.LEFT, padx=5)

        ao_channels = controller.get_available_channels()
        channel_var = tk.StringVar(value=ao_channels[0] if ao_channels else "")
        channel_menu = ttk.Combobox(
            ch_frame, textvariable=channel_var,
            values=ao_channels, state='readonly', width=20
        )
        channel_menu.pack(side=tk.LEFT, padx=5)

        # Signal type
        st_frame = tk.Frame(ao_frame, bg='#333333')
        st_frame.pack(fill=tk.X, pady=2)

        tk.Label(
            st_frame, text="Signal:", bg='#333333', fg='white'
        ).pack(side=tk.LEFT, padx=5)

        signal_var = tk.StringVar(value="DC")
        signal_menu = ttk.Combobox(
            st_frame, textvariable=signal_var,
            values=["DC", "AC"], state='readonly', width=10
        )
        signal_menu.pack(side=tk.LEFT, padx=5)

        ac_capable = controller.is_hardware_ac_capable()
        if not ac_capable:
            tk.Label(
                st_frame, text="(AC requires ext. HW)",
                bg='#333333', fg='#ff8800', font=('TkDefaultFont', 8)
            ).pack(side=tk.LEFT, padx=5)

        # Waveform (for AC)
        wf_frame = tk.Frame(ao_frame, bg='#333333')
        wf_frame.pack(fill=tk.X, pady=2)

        tk.Label(
            wf_frame, text="Waveform:", bg='#333333', fg='white'
        ).pack(side=tk.LEFT, padx=5)

        wf_var = tk.StringVar(value="Sine")
        wf_menu = ttk.Combobox(
            wf_frame, textvariable=wf_var,
            values=[w.value for w in WaveformType],
            state='readonly', width=15
        )
        wf_menu.pack(side=tk.LEFT, padx=5)

        # Parameters
        param_frame = tk.Frame(ao_frame, bg='#333333')
        param_frame.pack(fill=tk.X, pady=2)

        # Value
        tk.Label(
            param_frame, text="Value (V):", bg='#333333', fg='white'
        ).grid(row=0, column=0, padx=5, pady=1, sticky='w')

        value_var = tk.StringVar(value="1.0")
        tk.Entry(param_frame, textvariable=value_var, width=10).grid(
            row=0, column=1, padx=5, pady=1
        )

        # Frequency
        tk.Label(
            param_frame, text="Freq (Hz):", bg='#333333', fg='white'
        ).grid(row=0, column=2, padx=5, pady=1, sticky='w')

        freq_var = tk.StringVar(value="60")
        tk.Entry(param_frame, textvariable=freq_var, width=10).grid(
            row=0, column=3, padx=5, pady=1
        )

        # Amplitude
        tk.Label(
            param_frame, text="Amp (V):", bg='#333333', fg='white'
        ).grid(row=1, column=0, padx=5, pady=1, sticky='w')

        amp_var = tk.StringVar(value="1.0")
        tk.Entry(param_frame, textvariable=amp_var, width=10).grid(
            row=1, column=1, padx=5, pady=1
        )

        # Offset
        tk.Label(
            param_frame, text="Offset (V):", bg='#333333', fg='white'
        ).grid(row=1, column=2, padx=5, pady=1, sticky='w')

        offset_var = tk.StringVar(value="0.0")
        tk.Entry(param_frame, textvariable=offset_var, width=10).grid(
            row=1, column=3, padx=5, pady=1
        )

        # Buttons
        btn_frame = tk.Frame(ao_frame, bg='#333333')
        btn_frame.pack(fill=tk.X, pady=5)

        ao_status_label = tk.Label(
            ao_frame, text="Output: Stopped",
            font=('TkDefaultFont', 10, 'bold'),
            bg='#1a1a1a', fg='#888888',
            height=2
        )
        ao_status_label.pack(fill=tk.X, padx=5, pady=2)

        def _start_output():
            """Start analog output."""
            try:
                channel = channel_var.get()
                signal = signal_var.get()
                value = float(value_var.get())
                freq = float(freq_var.get())
                amp = float(amp_var.get())
                offset = float(offset_var.get())

                task_name = None
                if signal == "DC":
                    task_name = controller.start_dc_output(
                        channel, value
                    )
                elif ac_capable and signal == "AC":
                    wf_type = WaveformType(wf_var.get())
                    task_name = controller.start_ac_output(
                        channel, wf_type, freq, amp, offset
                    )

                if task_name:
                    ao_status_label.configure(
                        text=f"Output: Active ({signal})",
                        fg='#00ff00'
                    )
                    start_out_btn.configure(state=tk.DISABLED)
                    stop_out_btn.configure(state=tk.NORMAL)
                else:
                    messagebox.showerror(
                        "Output Failed",
                        "Failed to start output. Check parameters."
                    )
            except Exception as ex:
                log.error("Output start error: %s", ex)
                messagebox.showerror("Error", str(ex))

        def _stop_output():
            """Stop analog output."""
            outputs = controller.get_active_outputs()
            for out in outputs:
                controller.stop_output(out['task_name'])
            ao_status_label.configure(
                text="Output: Stopped", fg='#888888'
            )
            start_out_btn.configure(state=tk.NORMAL)
            stop_out_btn.configure(state=tk.DISABLED)

        start_out_btn = tk.Button(
            btn_frame, text="▶ Start Output",
            command=_start_output,
            bg='#8b5a00', fg='white', width=12
        )
        start_out_btn.pack(side=tk.LEFT, padx=2)

        stop_out_btn = tk.Button(
            btn_frame, text="■ Stop Output",
            command=_stop_output,
            bg='#8b0000', fg='white', width=12,
            state=tk.DISABLED
        )
        stop_out_btn.pack(side=tk.LEFT, padx=2)

    def _create_digital_io_ui(self,
                               module_info: ModuleInfo,
                               section: CollapsibleFrame) -> None:
        """
        Create digital I/O UI controls.

        Args:
            module_info: Module information
            section: Parent collapsible section
        """
        controller = DigitalIOController(
            self.task_manager, module_info
        )
        self._digital_controllers[module_info.name] = controller

        content = section.content
        dio_frame = tk.LabelFrame(
            content, text="Digital I/O",
            font=('TkDefaultFont', 9, 'bold'),
            bg='#333333', fg='#00aaff',
            padx=10, pady=5
        )
        dio_frame.pack(fill=tk.X, padx=5, pady=5)

        # Digital Input section
        if controller.has_digital_input():
            di_frame = tk.Frame(dio_frame, bg='#333333', bd=1, relief=tk.SUNKEN)
            di_frame.pack(fill=tk.X, padx=5, pady=5)

            tk.Label(
                di_frame, text="Digital Input",
                font=('TkDefaultFont', 8, 'bold'),
                bg='#1a1a1a', fg='#00aaff'
            ).pack(fill=tk.X, padx=5, pady=2)

            di_value = tk.Label(
                di_frame, text="--",
                font=('TkDefaultFont', 10),
                bg='#1a1a1a', fg='#00ff00',
                height=2
            )
            di_value.pack(fill=tk.X, padx=5, pady=2)

            def _read_di():
                """Read digital input values."""
                values = controller.read_digital_input()
                if values:
                    text = " | ".join(
                        f"{ch.split('/')[-1]}: {'HIGH' if v else 'LOW'}"
                        for ch, v in values.items()
                    )
                    di_value.configure(text=text)

            tk.Button(
                di_frame, text="Read DI",
                command=_read_di,
                bg='#1a4a6d', fg='white', width=10
            ).pack(side=tk.LEFT, padx=5, pady=2)

        # Digital Output section
        if controller.has_digital_output():
            do_frame = tk.Frame(dio_frame, bg='#333333', bd=1, relief=tk.SUNKEN)
            do_frame.pack(fill=tk.X, padx=5, pady=5)

            tk.Label(
                do_frame, text="Digital Output",
                font=('TkDefaultFont', 8, 'bold'),
                bg='#1a1a1a', fg='#00aaff'
            ).pack(fill=tk.X, padx=5, pady=2)

            # Create toggle buttons for each DO channel
            do_channels = controller.get_do_channels()
            do_btn_frame = tk.Frame(do_frame, bg='#333333')
            do_btn_frame.pack(fill=tk.X, padx=5, pady=2)

            do_buttons = {}

            def _make_toggle(ch_name: str) -> Callable:
                """Create toggle function for a channel."""
                def toggle():
                    state = controller.toggle_output(ch_name)
                    if state is not None:
                        btn = do_buttons.get(ch_name)
                        if btn:
                            color = '#00ff00' if state else '#444444'
                            btn.configure(bg=color)
                return toggle

            for ch in do_channels[:16]:  # Limit to 16 channels for UI
                short_name = ch.name.split('/')[-1]
                btn = tk.Button(
                    do_btn_frame, text=short_name,
                    command=_make_toggle(ch.name),
                    bg='#444444', fg='white',
                    width=6, height=1
                )
                btn.pack(side=tk.LEFT, padx=1, pady=1)
                do_buttons[ch.name] = btn

    def _create_counter_ui(self,
                            module_info: ModuleInfo,
                            section: CollapsibleFrame) -> None:
        """
        Create counter/timer UI controls.

        Args:
            module_info: Module information
            section: Parent collapsible section
        """
        controller = DigitalIOController(
            self.task_manager, module_info
        )
        if module_info.name not in self._digital_controllers:
            self._digital_controllers[module_info.name] = controller

        content = section.content
        ct_frame = tk.LabelFrame(
            content, text="Counter / Timer",
            font=('TkDefaultFont', 9, 'bold'),
            bg='#333333', fg='#ff66cc',
            padx=10, pady=5
        )
        ct_frame.pack(fill=tk.X, padx=5, pady=5)

        # Counter channels
        counters = controller.get_counter_channels()
        if counters:
            cnt_frame = tk.Frame(ct_frame, bg='#333333')
            cnt_frame.pack(fill=tk.X, padx=5, pady=2)

            for counter in counters:
                c_frame = tk.Frame(cnt_frame, bg='#2a2a2a', bd=1, relief=tk.RIDGE)
                c_frame.pack(fill=tk.X, padx=2, pady=2)

                tk.Label(
                    c_frame, text=counter.name.split('/')[-1],
                    font=('TkDefaultFont', 8, 'bold'),
                    bg='#2a2a2a', fg='#ff66cc'
                ).pack(side=tk.LEFT, padx=5)

                counter_value = tk.Label(
                    c_frame, text="0", width=15,
                    font=('TkDefaultFont', 10),
                    bg='#1a1a1a', fg='#00ff00'
                )
                counter_value.pack(side=tk.LEFT, padx=5)

                def _make_read_counter(ct_name: str, label: tk.Label):
                    """Create counter read function."""
                    def read():
                        val = controller.read_counter(ct_name)
                        if val is not None:
                            label.configure(text=f"{val:.0f}")
                    return read

                tk.Button(
                    c_frame, text="Read",
                    command=_make_read_counter(counter.name, counter_value),
                    bg='#4a1a4a', fg='white', width=6
                ).pack(side=tk.RIGHT, padx=2)

    def get_analog_controller(self,
                               module_name: str) -> Optional[AnalogInputController]:
        """
        Get the analog input controller for a module.

        Args:
            module_name: Module name

        Returns:
            AnalogInputController if exists, None otherwise
        """
        return self._analog_controllers.get(module_name)

    def get_output_controller(self,
                               module_name: str) -> Optional[AnalogOutputController]:
        """
        Get the analog output controller for a module.

        Args:
            module_name: Module name

        Returns:
            AnalogOutputController if exists, None otherwise
        """
        return self._output_controllers.get(module_name)

    def get_digital_controller(self,
                                module_name: str) -> Optional[DigitalIOController]:
        """
        Get the digital I/O controller for a module.

        Args:
            module_name: Module name

        Returns:
            DigitalIOController if exists, None otherwise
        """
        return self._digital_controllers.get(module_name)

    def cleanup(self) -> None:
        """Clean up all controllers."""
        for controller in self._analog_controllers.values():
            controller.cleanup()
        for controller in self._output_controllers.values():
            controller.cleanup()
        for controller in self._digital_controllers.values():
            controller.cleanup()
        log.info("ModuleUIFactory cleaned up")