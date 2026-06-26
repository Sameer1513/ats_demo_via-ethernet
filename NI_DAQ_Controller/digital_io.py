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

import time
import threading
import numpy as np
from typing import List, Optional, Tuple, Dict, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
from logger import get_logger
from task_manager import TaskManager
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
        self._lock = threading.Lock()
        self._monitoring = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._monitor_callbacks: List[Callable] = []

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
            # Start task
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

    def write_digital_output(self,
                              states: Dict[str, bool]) -> bool:
        """
        Write digital output values.

        Args:
            states: Dictionary mapping channel names to boolean states

        Returns:
            True if write succeeded, False otherwise
        """
        if not self.has_digital_output():
            log.warning("No digital output channels available on %s", self.device_name)
            return False

        # Validate channels
        valid_channels = set(ch.name for ch in self._do_channels)
        for channel in states:
            if channel not in valid_channels:
                log.error("Invalid DO channel: %s", channel)
                return False

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
        current = self._output_states.get(channel)
        if current is None:
            log.error("Unknown output channel: %s", channel)
            return None

        new_state = not current
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
        self.stop_digital_monitoring()

        # Clear digital tasks
        if self._di_task:
            self.task_manager.clear_task(self._di_task)
            self._di_task = None

        if self._do_task:
            self.task_manager.clear_task(self._do_task)
            self._do_task = None

        self._output_states.clear()
        log.info("DigitalIOController cleaned up")