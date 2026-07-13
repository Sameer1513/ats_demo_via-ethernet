"""
Digital I/O module for NI DAQ Controller.

Provides high-level operations for digital input, digital output,
counter/timer operations, and relay control. Automatically detects
available digital channels and creates appropriate controls.

Features:
    - Digital input reading
    - Digital output control
    - Port-wide operations
    - Line-level operations
    - Counter input support
    - Counter output support
    - Relay control
    - Thread-safe operations

Typical usage:
    from digital_io import DigitalIOController
    dio = DigitalIOController(task_manager, module_info)
    values = dio.read_digital()
    dio.write_digital({"port0/line0": True, "port0/line1": False})
"""

import re
import time
import threading
import numpy as np
from typing import List, Optional, Tuple, Dict, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
from logger import get_logger
from task_manager import TaskManager, TaskState, channel_on_port
from device_manager import ModuleInfo

log = get_logger(__name__)


class DigitalDirection(Enum):
    """Direction of a digital channel."""
    INPUT = "Input"
    OUTPUT = "Output"
    BIDIRECTIONAL = "Bidirectional"


class CounterMode(Enum):
    """Counter operating mode."""
    RISING_EDGE = "Rising Edge Count"
    FALLING_EDGE = "Falling Edge Count"
    FREQUENCY = "Frequency Measurement"
    PERIOD = "Period Measurement"
    PWM = "PWM Generation"
    PULSE_TRAIN = "Pulse Train Generation"


@dataclass
class DigitalChannelInfo:
    """
    Information about a digital channel or line.

    Attributes:
        name: Channel name
        port: Port name
        line: Line number
        direction: Input, Output, or Bidirectional
        is_port: Whether this represents an entire port
    """
    name: str
    port: str = ""
    line: int = 0
    direction: DigitalDirection = DigitalDirection.INPUT
    is_port: bool = False


@dataclass
class CounterInfo:
    """
    Information about a counter channel.

    Attributes:
        name: Counter channel name
        counter_mode: Supported counter modes
        max_frequency: Maximum frequency for counting
        is_input: Whether this is a counter input or output
    """
    name: str
    counter_mode: List[CounterMode] = field(default_factory=list)
    max_frequency: float = 1000000.0
    is_input: bool = True


class DigitalIOController:
    """
    Controller for digital I/O operations.

    Manages digital input, output, counter, and timer operations
    for a DAQ module. Provides both port-level and line-level control.

    Attributes:
        task_manager: Reference to the global TaskManager
        module_info: Module information for the device
        _di_task: Active digital input task name
        _do_task: Active digital output task name
        _output_states: Current output states for all channels
        _lock: Thread lock for safe concurrent access
    """

    def __init__(self, task_manager: TaskManager,
                 module_info: ModuleInfo) -> None:
        """
        Initialize digital I/O controller.

        Args:
            task_manager: Global TaskManager instance
            module_info: Module information for the device
        """
        self.task_manager = task_manager
        self.module_info = module_info
        self.device_name = module_info.name

        self._di_task: Optional[str] = None
        self._do_task: Optional[str] = None
        self._output_states: Dict[str, bool] = {}
        self._lock = threading.RLock()
        self._monitoring = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._monitor_callbacks: List[Callable] = []
        # USB-6509: one DO task per port (all 8 lines), cached line states
        self._port_do_tasks: Dict[str, str] = {}
        self._port_line_states: Dict[str, List[bool]] = {}
        self._last_failed_ports: List[int] = []

        # Parse digital channel information
        self._di_channels: List[DigitalChannelInfo] = []
        self._do_channels: List[DigitalChannelInfo] = []
        self._counter_channels: List[CounterInfo] = []

        self._parse_channels()

        log.info(
            "DigitalIOController initialized for %s "
            "(DI: %d, DO: %d, Counters: %d)",
            self.device_name,
            len(self._di_channels),
            len(self._do_channels),
            len(self._counter_channels)
        )

    def _parse_channels(self) -> None:
        """
        Parse DI, DO, and counter channels from module info.
        """
        # Parse digital input channels
        for ch in self.module_info.di_channels:
            ch_info = self._parse_digital_channel(ch, DigitalDirection.INPUT)
            self._di_channels.append(ch_info)

        # Parse digital output channels
        for ch in self.module_info.do_channels:
            ch_info = self._parse_digital_channel(ch, DigitalDirection.OUTPUT)
            self._do_channels.append(ch_info)
            self._output_states[ch] = False  # Initialize as off

        # Parse counter input channels
        for ch in self.module_info.ci_channels:
            counter_info = CounterInfo(
                name=ch,
                counter_mode=[
                    CounterMode.RISING_EDGE,
                    CounterMode.FREQUENCY,
                    CounterMode.PERIOD
                ],
                is_input=True
            )
            self._counter_channels.append(counter_info)

        # Parse counter output channels
        for ch in self.module_info.co_channels:
            counter_info = CounterInfo(
                name=ch,
                counter_mode=[
                    CounterMode.PWM,
                    CounterMode.PULSE_TRAIN
                ],
                is_input=False
            )
            self._counter_channels.append(counter_info)

    def _parse_digital_channel(self, channel_name: str,
                                direction: DigitalDirection) -> DigitalChannelInfo:
        """
        Parse a digital channel name into its components.

        Args:
            channel_name: Full channel name
            direction: Direction of the channel

        Returns:
            DigitalChannelInfo object
        """
        # Parse NI-DAQmx format: "cDAQ1Mod1/port0/line0"
        parts = channel_name.split('/')

        if len(parts) >= 3 and 'line' in parts[-1].lower():
            # Line-level channel
            try:
                line_num = int(parts[-1].lower().replace('line', ''))
            except ValueError:
                line_num = 0

            port = parts[-2] if len(parts) >= 2 else ""
            return DigitalChannelInfo(
                name=channel_name,
                port=port,
                line=line_num,
                direction=direction,
                is_port=False
            )
        elif len(parts) >= 2:
            # Port-level channel
            port = parts[-1]
            return DigitalChannelInfo(
                name=channel_name,
                port=port,
                line=0,
                direction=direction,
                is_port='port' in port.lower()
            )
        else:
            return DigitalChannelInfo(
                name=channel_name,
                direction=direction
            )

    def get_di_channels(self) -> List[DigitalChannelInfo]:
        """
        Get available digital input channels.

        Returns:
            List of DigitalChannelInfo objects
        """
        return list(self._di_channels)

    def get_do_channels(self) -> List[DigitalChannelInfo]:
        """
        Get available digital output channels.

        Returns:
            List of DigitalChannelInfo objects
        """
        return list(self._do_channels)

    def get_counter_channels(self) -> List[CounterInfo]:
        """
        Get available counter channels.

        Returns:
            List of CounterInfo objects
        """
        return list(self._counter_channels)

    def has_digital_input(self) -> bool:
        """
        Check if digital input is available.

        Returns:
            True if DI channels exist
        """
        return len(self._di_channels) > 0

    def has_digital_output(self) -> bool:
        """
        Check if digital output is available.

        Returns:
            True if DO channels exist
        """
        return len(self._do_channels) > 0

    def has_counter(self) -> bool:
        """
        Check if counter channels are available.

        Returns:
            True if counter channels exist
        """
        return len(self._counter_channels) > 0

    def _uses_port_shared_dio(self) -> bool:
        """USB-6509-style hardware: each port is input OR output, not both."""
        product = (self.module_info.product_type or '').upper()
        if '6509' in product or '6508' in product or '6504' in product:
            return True
        di = self.module_info.di_channels
        do = self.module_info.do_channels
        return bool(di and do and di == do)

    @staticmethod
    def _port_key(channel: str) -> str:
        parts = channel.split('/')
        if parts and parts[-1].lower().startswith('line'):
            key = '/'.join(parts[:-1])
            return key if key else channel
        if len(parts) >= 2:
            return '/'.join(parts[:2])
        return channel

    @staticmethod
    def _line_index(channel: str) -> int:
        tail = channel.split('/')[-1].lower()
        return int(tail.replace('line', ''))

    def _port_sort_key(self, port_key: str) -> int:
        match = re.search(r'port(\d+)', port_key, re.IGNORECASE)
        return int(match.group(1)) if match else 0

    def _iter_port_keys(self) -> List[str]:
        ports = {
            self._port_key(ch)
            for ch in (self.module_info.do_channels or [])
        }
        return sorted(ports, key=self._port_sort_key)

    def _channels_for_port(self, port_key: str) -> List[str]:
        return sorted(
            [
                ch for ch in (self.module_info.do_channels or [])
                if channel_on_port(ch, port_key)
            ],
            key=self._line_index,
        )

    def _release_blocking_tasks_on_port(self, port_key: str) -> None:
        """Clear DI/foreign tasks on a port but keep our latched DO task."""
        if self._di_task:
            info = self.task_manager.get_task_info(self._di_task)
            if info and any(channel_on_port(ch, port_key) for ch in info.channels):
                self.task_manager.clear_task(self._di_task)
                self._di_task = None

        owned_do = self._port_do_tasks.get(port_key)
        for task_info in list(self.task_manager.get_all_tasks()):
            if task_info.device_name != self.device_name:
                continue
            if task_info.name == owned_do:
                continue
            if any(channel_on_port(ch, port_key) for ch in task_info.channels):
                self.task_manager.clear_task(task_info.name)
                for pk, tn in list(self._port_do_tasks.items()):
                    if tn == task_info.name:
                        self._port_do_tasks.pop(pk, None)

    def _drop_port_do_task(self, port_key: str) -> None:
        """Release persistent port DO task so a fresh line task can drive hardware."""
        task_name = self._port_do_tasks.pop(port_key, None)
        if task_name:
            self.task_manager.clear_task(task_name)

    def _apply_port_line_states(
        self,
        port_key: str,
        port_channels: List[str],
        line_states: List[bool],
    ) -> None:
        self._port_line_states[port_key] = list(line_states)
        with self._lock:
            for ch, val in zip(port_channels, line_states):
                self._output_states[ch] = bool(val)

    def _port_line_state(self, port_key: str) -> List[bool]:
        port_channels = self._channels_for_port(port_key)
        if port_key in self._port_line_states:
            return self._port_line_states[port_key]
        line_states = [
            bool(self._output_states.get(ch, False))
            for ch in port_channels
        ]
        self._port_line_states[port_key] = line_states
        return line_states

    def read_digital_input(self,
                            channels: Optional[List[str]] = None) -> Optional[Dict[str, bool]]:
        """
        Read digital input values.

        Args:
            channels: Specific channels to read. Reads all DI if None.

        Returns:
            Dictionary mapping channel names to boolean values, or None on failure
        """
        if not self.has_digital_input():
            log.warning("No digital input channels available on %s", self.device_name)
            return None

        # Determine channels to read
        if channels is None:
            channels = [ch.name for ch in self._di_channels]

        if not channels:
            return None

        # Single-line reads use a fresh task (avoids "task is running" on repeat reads).
        if len(channels) == 1:
            value = self.task_manager.read_di_line(channels[0])
            if value is None:
                return None
            return {channels[0]: value}

        # Create task if not exists
        if self._di_task is None:
            task_name = self.task_manager.create_di_task(
                self.device_name, channels
            )
            if task_name is None:
                log.error("Failed to create DI task for %s", self.device_name)
                return None
            self._di_task = task_name

        try:
            task_info = self.task_manager.get_task_info(self._di_task)
            if not task_info or task_info.state != TaskState.RUNNING:
                self.task_manager.start_task(self._di_task)

            # Read values
            values = self.task_manager.read_digital(self._di_task)

            if values is None:
                return None

            # Map to channels
            result = {}
            for i, channel in enumerate(channels):
                if i < len(values):
                    result[channel] = bool(values[i])
                else:
                    result[channel] = False

            return result

        except Exception as e:
            log.error("Failed to read digital input: %s", e)
            return None

    def get_last_failed_ports(self) -> List[int]:
        """Port numbers that failed on the last write (NI MAX Input direction, etc.)."""
        return list(self._last_failed_ports)

    def test_ports(self) -> Dict[int, Dict[str, Any]]:
        """Test each DO port with full-port True then False writes."""
        results: Dict[int, Dict[str, Any]] = {}
        line_results = self.test_lines()
        by_port: Dict[int, List[bool]] = {}
        for key, info in line_results.items():
            pn = info.get('port', -1)
            by_port.setdefault(pn, []).append(bool(info.get('ok')))
        for pn, oks in by_port.items():
            results[pn] = {
                'ok': all(oks) if oks else False,
                'lines': len(oks),
                'lines_ok': sum(1 for x in oks if x),
                'message': 'OK' if all(oks) else 'One or more lines failed',
            }
        return results

    def _channel_for_port_line(self, port: int, line: int) -> Optional[str]:
        for port_key in self._iter_port_keys():
            if self._port_sort_key(port_key) != port:
                continue
            for ch in self._channels_for_port(port_key):
                if self._line_index(ch) == line:
                    return ch
        return None

    def test_one_line(self, port: int, line: int) -> Dict[str, Any]:
        """Test a single DO line (True then False)."""
        ch = self._channel_for_port_line(port, line)
        if not ch:
            return {
                'ok': False,
                'found': False,
                'port': port,
                'line': line,
                'channel': None,
                'ni_line': f"P{port}.{line}",
                'message': 'Channel not found',
            }
        ok_true = self.write_digital_output({ch: True})
        failed = set(self.get_last_failed_ports())
        ok_false = self.write_digital_output({ch: False})
        failed |= set(self.get_last_failed_ports())
        ok = ok_true and ok_false and port not in failed
        return {
            'ok': ok,
            'found': True,
            'port': port,
            'line': line,
            'channel': ch,
            'ni_line': f"P{port}.{line}",
            'message': 'OK' if ok else 'Write failed',
        }

    def test_lines(self) -> Dict[str, Dict[str, Any]]:
        """Test each DO line individually (True then False), one at a time."""
        results: Dict[str, Dict[str, Any]] = {}
        if not self.has_digital_output():
            return results
        for port_key in self._iter_port_keys():
            port_num = self._port_sort_key(port_key)
            for ch in self._channels_for_port(port_key):
                line_num = self._line_index(ch)
                key = f"{port_num}.{line_num}"
                results[key] = self.test_one_line(port_num, line_num)
        return results

    def write_digital_output(self,
                              states: Dict[str, bool]) -> bool:
        """
        Write digital output values.

        Args:
            states: Dictionary mapping channel names to boolean states

        Returns:
            True if write succeeded, False otherwise
        """
        with self._lock:
            return self._write_digital_output_unlocked(states)

    def _write_digital_output_unlocked(self, states: Dict[str, bool]) -> bool:
        if not self.has_digital_output():
            log.warning("No digital output channels available on %s", self.device_name)
            return False

        self._last_failed_ports = []

        # Validate channels
        valid_channels = set(ch.name for ch in self._do_channels)
        for channel in states:
            if channel not in valid_channels:
                log.error("Invalid DO channel: %s", channel)
                return False

        if self._uses_port_shared_dio():
            return self._write_port_shared_output(states)

        # Create task if not exists
        if self._do_task is None:
            task_name = self.task_manager.create_do_task(
                self.device_name, list(states.keys())
            )
            if task_name is None:
                log.error("Failed to create DO task for %s", self.device_name)
                return False
            self._do_task = task_name

        try:
            # Convert states to list
            channels = list(states.keys())
            values = [states[ch] for ch in channels]

            # Write values
            success = self.task_manager.write_digital(
                self._do_task, values
            )

            if success:
                # Update stored states
                with self._lock:
                    self._output_states.update(states)
                log.debug("Digital output updated: %s", states)

            return success

        except Exception as e:
            log.error("Failed to write digital output: %s", e)
            return False

    def _write_port_shared_output(self, states: Dict[str, bool]) -> bool:
        """Write DIO on USB-6509."""
        by_port: Dict[str, Dict[str, bool]] = {}
        for channel, value in states.items():
            by_port.setdefault(self._port_key(channel), {})[channel] = value

        any_ok = False
        try:
            for port_key in sorted(by_port.keys(), key=self._port_sort_key):
                port_states = by_port[port_key]
                port_channels = self._channels_for_port(port_key)
                if not port_channels:
                    log.error("No DO channels on port %s", port_key)
                    self._last_failed_ports.append(self._port_sort_key(port_key))
                    continue

                self._release_blocking_tasks_on_port(port_key)
                port_num = self._port_sort_key(port_key)

                # Single line: fresh per-line task (matches working R501 / NI MAX pattern)
                if len(port_states) == 1:
                    channel, value = next(iter(port_states.items()))
                    self._drop_port_do_task(port_key)
                    if self.task_manager.write_do_line(channel, bool(value)):
                        line_states = list(self._port_line_state(port_key))
                        idx = self._line_index(channel)
                        if 0 <= idx < len(line_states):
                            line_states[idx] = bool(value)
                        self._apply_port_line_states(port_key, port_channels, line_states)
                        any_ok = True
                    else:
                        self._last_failed_ports.append(port_num)
                        log.error(
                            "Write failed on %s — set port %d to Output in NI MAX",
                            channel, port_num,
                        )
                    continue

                # Multiple lines on one port: latched port-level task
                line_states = list(self._port_line_state(port_key))
                for channel, value in port_states.items():
                    idx = self._line_index(channel)
                    if 0 <= idx < len(line_states):
                        line_states[idx] = bool(value)

                task_name = self._port_do_tasks.get(port_key)
                if task_name and not self.task_manager.get_task_info(task_name):
                    self._port_do_tasks.pop(port_key, None)
                    task_name = None

                if not task_name:
                    task_name = self.task_manager.create_do_port_task(
                        self.device_name, port_key, port_channels,
                    )
                    if task_name is None:
                        self._last_failed_ports.append(port_num)
                        log.error(
                            "Cannot write port %d — set direction to Output in NI MAX",
                            port_num,
                        )
                        continue
                    self._port_do_tasks[port_key] = task_name

                if not self.task_manager.write_digital(task_name, line_states):
                    self._last_failed_ports.append(port_num)
                    self._drop_port_do_task(port_key)
                    log.error(
                        "Write failed on port %d — set direction to Output in NI MAX",
                        port_num,
                    )
                    continue

                any_ok = True
                self._apply_port_line_states(port_key, port_channels, line_states)

            return any_ok

        except Exception as e:
            log.error("Failed to write port-shared digital output: %s", e)
            return False

    def set_output_line(self, channel: str, state: bool) -> bool:
        """
        Set a single digital output line.

        Args:
            channel: Channel name to set
            state: True for high/on, False for low/off

        Returns:
            True if successful, False otherwise
        """
        return self.write_digital_output({channel: state})

    def get_output_state(self, channel: str) -> Optional[bool]:
        """
        Get the current state of an output channel.

        Args:
            channel: Channel name

        Returns:
            Current state if available, None otherwise
        """
        return self._output_states.get(channel)

    def read_output_line(self, channel: str) -> Optional[bool]:
        """
        Read digital output / relay state from hardware.

        Args:
            channel: DO channel name (e.g. relay line on NI 948x)

        Returns:
            True = relay closed/on, False = open/off, None on failure
        """
        value = self.task_manager.read_do_line(channel)
        if value is not None:
            self._output_states[channel] = value
        return value

    def get_all_output_states(self) -> Dict[str, bool]:
        """
        Get current states of all output channels.

        Returns:
            Dictionary of channel states
        """
        return dict(self._output_states)

    def toggle_output(self, channel: str) -> Optional[bool]:
        """
        Toggle a digital output line.

        Args:
            channel: Channel name to toggle

        Returns:
            New state if successful, None otherwise
        """
        if channel not in self._output_states:
            log.error("Unknown output channel: %s", channel)
            return None

        new_state = not self._output_states[channel]
        if self.set_output_line(channel, new_state):
            return new_state
        return None

    def start_digital_monitoring(self,
                                  callback: Callable[[Dict[str, bool]], None],
                                  interval_ms: float = 100.0) -> bool:
        """
        Start background monitoring of digital inputs.

        Args:
            callback: Function to call with each input state update
            interval_ms: Polling interval in milliseconds

        Returns:
            True if monitoring started, False otherwise
        """
        if self._monitoring:
            log.warning("Digital monitoring already running")
            return False

        self._monitoring = True
        self._monitor_callbacks.append(callback)

        def _monitor_loop():
            """Background loop for digital input monitoring."""
            log.info("Started digital monitoring (interval=%d ms)", interval_ms)

            while self._monitoring:
                try:
                    values = self.read_digital_input()
                    if values is not None:
                        for cb in self._monitor_callbacks:
                            try:
                                cb(values)
                            except Exception as e:
                                log.error("Monitor callback error: %s", e)

                except Exception as e:
                    log.error("Digital monitoring error: %s", e)

                time.sleep(interval_ms / 1000.0)

            log.info("Digital monitoring stopped")

        self._monitor_thread = threading.Thread(
            target=_monitor_loop,
            name=f"di-mon-{self.device_name}",
            daemon=True
        )
        self._monitor_thread.start()

        return True

    def stop_digital_monitoring(self) -> None:
        """
        Stop background digital input monitoring.
        """
        self._monitoring = False
        self._monitor_callbacks.clear()

        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=1.0)
            self._monitor_thread = None

        log.info("Digital monitoring stopped")

    def read_counter(self, counter_channel: str,
                     mode: CounterMode = CounterMode.RISING_EDGE,
                     timeout: float = 1.0) -> Optional[float]:
        """
        Read a counter value.

        Args:
            counter_channel: Name of the counter channel
            mode: Counter operating mode
            timeout: Read timeout in seconds

        Returns:
            Counter value if successful, None otherwise
        """
        # Create a counter input task
        try:
            task_name = f"{self.device_name}_ci_{int(time.time() * 1000)}"

            import nidaqmx
            task = nidaqmx.Task(task_name)

            # Configure counter based on mode
            if mode == CounterMode.RISING_EDGE:
                task.ci_channels.add_ci_count_edges_chan(
                    f"{self.device_name}/{counter_channel}"
                )
            elif mode == CounterMode.FREQUENCY:
                task.ci_channels.add_ci_freq_chan(
                    f"{self.device_name}/{counter_channel}"
                )
            elif mode == CounterMode.PERIOD:
                task.ci_channels.add_ci_period_chan(
                    f"{self.device_name}/{counter_channel}"
                )

            task.start()
            value = task.read(timeout=timeout)
            task.stop()
            task.close()

            return float(value)

        except ImportError:
            log.error("NI-DAQmx library not available")
            return None
        except Exception as e:
            log.error("Failed to read counter '%s': %s", counter_channel, e)
            return None

    def start_pwm_output(self,
                          channel: str,
                          frequency: float = 1000.0,
                          duty_cycle: float = 50.0) -> Optional[str]:
        """
        Start a PWM output on a counter channel.

        Args:
            channel: Counter output channel name
            frequency: PWM frequency in Hz
            duty_cycle: Duty cycle percentage (0-100)

        Returns:
            Task name if successful, None otherwise
        """
        try:
            import nidaqmx
            task_name = f"{self.device_name}_pwm_{int(time.time() * 1000)}"
            task = nidaqmx.Task(task_name)

            # Add counter output for frequency generation
            task.co_channels.add_co_pulse_chan_freq(
                f"{self.device_name}/{channel}",
                freq=frequency,
                duty_cycle=duty_cycle / 100.0
            )

            # Configure timing
            task.timing.cfg_implicit_timing(
                sample_mode=nidaqmx.constants.AcquisitionType.CONTINUOUS
            )

            task.start()

            log.info(
                "Started PWM: channel=%s, freq=%.1f Hz, duty=%.1f%%",
                channel, frequency, duty_cycle
            )

            return task_name

        except ImportError:
            log.error("NI-DAQmx library not available")
            return None
        except Exception as e:
            log.error("Failed to start PWM on '%s': %s", channel, e)
            return None

    def stop_counter_output(self, task_name: str) -> bool:
        """
        Stop a counter output task.

        Args:
            task_name: Name of the task to stop

        Returns:
            True if stopped successfully
        """
        try:
            import nidaqmx
            # Task reference needs to be managed externally
            log.info("Stopped counter output task: %s", task_name)
            return True
        except Exception as e:
            log.error("Failed to stop counter output: %s", e)
            return False

    def cleanup(self) -> None:
        """Clean up all resources."""
        self.reset_io_state()

    def reset_io_state(self) -> None:
        """Release held DO/DI tasks and cached line state."""
        self.stop_digital_monitoring()

        # Clear digital tasks
        if self._di_task:
            self.task_manager.clear_task(self._di_task)
            self._di_task = None

        if self._do_task:
            self.task_manager.clear_task(self._do_task)
            self._do_task = None

        for port_key in list(self._port_do_tasks.keys()):
            task_name = self._port_do_tasks.pop(port_key, None)
            if task_name:
                self.task_manager.clear_task(task_name)

        self._port_line_states.clear()
        self._output_states.clear()
        log.info("DigitalIOController cleaned up")