"""
Analog Output module for NI DAQ Controller.

Provides high-level operations for analog output including DC/AC signal
generation with configurable waveforms. Supports voltage and current output
mode, various signal types, and continuous output generation.

Features:
    - DC voltage/current output
    - AC waveform generation (Sine, Square, Triangle, Sawtooth)
    - Configurable frequency, amplitude, offset, phase
    - Continuous output in background thread
    - Single-shot output
    - Stop output operations
    - Thread-safe operations

Typical usage:
    from analog_output import AnalogOutputController
    ao = AnalogOutputController(task_manager, module_info)
    task_name = ao.start_dc_output("ao0", 5.0)
    ao.stop_output(task_name)
"""

import time
import math
import threading
import numpy as np
from typing import List, Optional, Tuple, Dict, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
from logger import get_logger
from task_manager import TaskManager
from device_manager import ModuleInfo
from utils import validate_numeric_input

log = get_logger(__name__)


class SignalType(Enum):
    """Type of output signal."""
    DC = "DC"
    AC = "AC"


class OutputMode(Enum):
    """Output mode - voltage or current."""
    VOLTAGE = "Voltage"
    CURRENT = "Current"


class MeasurementMode(Enum):
    """Measurement mode for output configuration."""
    RMS = "RMS (Vrms)"
    PEAK = "Peak Voltage"
    PEAK_TO_PEAK = "Peak-to-Peak"
    DC = "Direct Current (DC)"


class WaveformType(Enum):
    """Supported waveform types for AC output."""
    CONSTANT = "Constant"
    SINE = "Sine"
    SQUARE = "Square"
    TRIANGLE = "Triangle"
    SAWTOOTH = "Sawtooth"


@dataclass
class OutputConfig:
    """
    Configuration for an analog output channel.

    Attributes:
        channel: Output channel name
        signal_type: DC or AC signal
        output_mode: Voltage or Current
        measurement_mode: RMS, Peak, Peak-to-Peak, DC
        waveform: Waveform type for AC
        frequency: Frequency in Hz (for AC)
        amplitude: Signal amplitude in volts
        offset: DC offset voltage
        phase: Phase offset in degrees
        duration: Output duration in seconds (0 = continuous)
        voltage_min: Minimum voltage range
        voltage_max: Maximum voltage range
    """
    channel: str
    signal_type: SignalType = SignalType.DC
    output_mode: OutputMode = OutputMode.VOLTAGE
    measurement_mode: MeasurementMode = MeasurementMode.DC
    waveform: WaveformType = WaveformType.CONSTANT
    frequency: float = 60.0
    amplitude: float = 1.0
    offset: float = 0.0
    phase: float = 0.0
    duration: float = 0.0
    voltage_min: float = -10.0
    voltage_max: float = 10.0


class AnalogOutputController:
    """
    Controller for analog output operations.

    Manages output tasks for DC and AC signal generation with
    various waveform types. Supports single-shot and continuous
    output modes with thread-safe operations.

    Attributes:
        task_manager: Reference to the global TaskManager
        module_info: Module information for the device
        _active_outputs: Dictionary tracking active output tasks
        _stop_events: Thread stop events for continuous output
        _lock: Thread lock for safe concurrent access
    """

    def __init__(self, task_manager: TaskManager,
                 module_info: ModuleInfo) -> None:
        """
        Initialize analog output controller.

        Args:
            task_manager: Global TaskManager instance
            module_info: Module information for the device
        """
        self.task_manager = task_manager
        self.module_info = module_info
        self.device_name = module_info.name

        self._active_outputs: Dict[str, OutputConfig] = {}
        self._stop_events: Dict[str, threading.Event] = {}
        self._output_threads: Dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

        log.info(
            "AnalogOutputController initialized for %s "
            "(%d AO channels)",
            self.device_name, len(module_info.ao_channels)
        )

    def get_available_channels(self) -> List[str]:
        """
        Get available analog output channels.

        Returns:
            List of channel names
        """
        return list(self.module_info.ao_channels)

    def get_voltage_ranges(self) -> List[Tuple[float, float]]:
        """
        Get supported voltage ranges.

        Returns:
            List of (min, max) voltage range tuples
        """
        if self.module_info.voltage_ranges:
            return self.module_info.voltage_ranges
        return [(-10.0, 10.0)]

    def is_hardware_ac_capable(self) -> bool:
        """
        Check if the hardware can natively generate AC signals.

        Most simple DAQ modules cannot generate AC waveforms natively;
        they require external signal conditioning. This method checks
        the module capabilities.

        Returns:
            True if hardware supports AC generation natively
        """
        # Most simple AO modules don't support native AC
        # This would need to be checked against specific module specs
        product = self.module_info.product_type.upper()

        # Modules known to support waveform generation
        ac_capable_keywords = [
            'FGEN', 'SIG GEN', 'WAVEFORM', 'ARBITRARY',
            'NI 9263', 'NI 9264', 'NI 9269'
        ]

        for keyword in ac_capable_keywords:
            if keyword in product:
                return True

        # Check max sample rate - higher rates suggest waveform capability
        if self.module_info.max_sample_rate > 10000:
            return True

        return False

    def start_dc_output(self, channel: str,
                        voltage: float,
                        voltage_min: float = -10.0,
                        voltage_max: float = 10.0) -> Optional[str]:
        """
        Start a DC voltage output on a channel.

        Args:
            channel: Output channel name
            voltage: DC voltage value to output
            voltage_min: Minimum voltage range
            voltage_max: Maximum voltage range

        Returns:
            Task name if successful, None otherwise
        """
        # Validate channel
        if channel not in self.module_info.ao_channels:
            log.error("Invalid AO channel: %s", channel)
            return None

        # Validate voltage
        if voltage < voltage_min or voltage > voltage_max:
            log.error(
                "Voltage %.2f out of range [%.1f, %.1f]",
                voltage, voltage_min, voltage_max
            )
            return None

        # Create output task
        task_name = self.task_manager.create_ao_task(
            device_name=self.device_name,
            channels=[channel],
            voltage_range=(voltage_min, voltage_max)
        )

        if task_name is None:
            return None

        # Store config
        config = OutputConfig(
            channel=channel,
            signal_type=SignalType.DC,
            output_mode=OutputMode.VOLTAGE,
            measurement_mode=MeasurementMode.DC,
            waveform=WaveformType.CONSTANT,
            amplitude=voltage,
            offset=0.0,
            voltage_min=voltage_min,
            voltage_max=voltage_max
        )

        with self._lock:
            self._active_outputs[task_name] = config

        # Write DC value
        data = np.array([voltage])
        success = self.task_manager.write_analog(task_name, data)

        if not success:
            log.error("Failed to write DC voltage to %s", channel)
            self.task_manager.clear_task(task_name)
            with self._lock:
                self._active_outputs.pop(task_name, None)
            return None

        log.info(
            "Started DC output: task=%s, channel=%s, voltage=%.3f V",
            task_name, channel, voltage
        )

        return task_name

    def start_ac_output(self,
                         channel: str,
                         waveform: WaveformType = WaveformType.SINE,
                         frequency: float = 60.0,
                         amplitude: float = 1.0,
                         offset: float = 0.0,
                         phase: float = 0.0,
                         duration: float = 0.0,
                         sample_rate: float = 10000.0,
                         voltage_min: float = -10.0,
                         voltage_max: float = 10.0) -> Optional[str]:
        """
        Start an AC waveform output on a channel.

        Generates waveform data and writes it to the output channel,
        either as a finite burst or continuously.

        Args:
            channel: Output channel name
            waveform: Type of waveform to generate
            frequency: Signal frequency in Hz
            amplitude: Signal amplitude in volts (peak)
            offset: DC offset voltage
            phase: Phase offset in degrees
            duration: Output duration in seconds (0 = continuous)
            sample_rate: Update rate in Hz for waveform generation
            voltage_min: Minimum voltage range
            voltage_max: Maximum voltage range

        Returns:
            Task name if successful, None otherwise
        """
        # Validate channel
        if channel not in self.module_info.ao_channels:
            log.error("Invalid AO channel: %s", channel)
            return None

        # Validate parameters
        if frequency <= 0:
            log.error("Frequency must be positive: %.2f", frequency)
            return None

        if amplitude <= 0:
            log.error("Amplitude must be positive: %.2f", amplitude)
            return None

        # Create output task
        task_name = self.task_manager.create_ao_task(
            device_name=self.device_name,
            channels=[channel],
            sample_rate=sample_rate,
            voltage_range=(voltage_min, voltage_max)
        )

        if task_name is None:
            return None

        # Store config
        config = OutputConfig(
            channel=channel,
            signal_type=SignalType.AC,
            output_mode=OutputMode.VOLTAGE,
            waveform=waveform,
            frequency=frequency,
            amplitude=amplitude,
            offset=offset,
            phase=phase,
            duration=duration,
            voltage_min=voltage_min,
            voltage_max=voltage_max
        )

        with self._lock:
            self._active_outputs[task_name] = config

        if duration > 0:
            # Finite burst mode
            num_samples = int(sample_rate * duration)
            data = self._generate_waveform(
                waveform, num_samples, sample_rate,
                frequency, amplitude, offset, phase
            )

            # Clip to voltage range
            data = np.clip(data, voltage_min, voltage_max)

            # Write data
            success = self.task_manager.write_analog(
                task_name, data, auto_start=True
            )

            if not success:
                log.error("Failed to write AC waveform to %s", channel)
                self.task_manager.clear_task(task_name)
                with self._lock:
                    self._active_outputs.pop(task_name, None)
                return None

            log.info(
                "Started AC burst: channel=%s, waveform=%s, "
                "freq=%.1f Hz, amp=%.3f V, duration=%.2f s",
                channel, waveform.value, frequency, amplitude, duration
            )
        else:
            # Continuous mode - start background thread
            stop_event = threading.Event()
            self._stop_events[task_name] = stop_event

            def _continuous_output():
                """Background thread for continuous waveform output."""
                samples_per_update = int(sample_rate / 10)  # 10 updates/sec
                phase_rad = math.radians(phase)
                t = 0.0

                while not stop_event.is_set():
                    data = self._generate_waveform(
                        waveform, samples_per_update, sample_rate,
                        frequency, amplitude, offset, phase_rad + t * 2 * math.pi * frequency
                    )
                    data = np.clip(data, voltage_min, voltage_max)

                    self.task_manager.write_analog(
                        task_name, data, auto_start=False
                    )

                    t += samples_per_update / sample_rate
                    stop_event.wait(1.0 / 10)  # 10 Hz update rate

            thread = threading.Thread(
                target=_continuous_output,
                name=f"ao-cont-{task_name}",
                daemon=True
            )
            thread.start()
            self._output_threads[task_name] = thread

            # Start the task
            self.task_manager.start_task(task_name)

            log.info(
                "Started continuous AC: channel=%s, waveform=%s, "
                "freq=%.1f Hz, amp=%.3f V",
                channel, waveform.value, frequency, amplitude
            )

        return task_name

    def stop_output(self, task_name: str) -> bool:
        """
        Stop an active output.

        Args:
            task_name: Name of the task to stop

        Returns:
            True if stopped successfully, False otherwise
        """
        # Stop continuous output thread
        if task_name in self._stop_events:
            self._stop_events[task_name].set()
            del self._stop_events[task_name]

        if task_name in self._output_threads:
            thread = self._output_threads.pop(task_name)
            if thread.is_alive():
                thread.join(timeout=1.0)

        # Stop and clear task
        self.task_manager.stop_task(task_name)
        self.task_manager.clear_task(task_name)

        # Clean up local state
        with self._lock:
            self._active_outputs.pop(task_name, None)

        log.info("Stopped output: %s", task_name)
        return True

    def stop_all_outputs(self) -> None:
        """Stop all active outputs."""
        with self._lock:
            task_names = list(self._active_outputs.keys())

        for task_name in task_names:
            self.stop_output(task_name)

        log.info("All outputs stopped")

    def update_dc_value(self, task_name: str,
                        voltage: float) -> bool:
        """
        Update the voltage of an active DC output.

        Args:
            task_name: Name of the output task
            voltage: New voltage value

        Returns:
            True if updated successfully, False otherwise
        """
        config = self._active_outputs.get(task_name)
        if config is None:
            log.error("Unknown output task: %s", task_name)
            return False

        if voltage < config.voltage_min or voltage > config.voltage_max:
            log.error(
                "Voltage %.2f out of range [%.1f, %.1f]",
                voltage, config.voltage_min, config.voltage_max
            )
            return False

        # Write new value
        data = np.array([voltage])
        success = self.task_manager.write_analog(
            task_name, data, auto_start=False
        )

        if success:
            config.amplitude = voltage
            log.debug("Updated DC output %s to %.3f V", task_name, voltage)
        else:
            log.error("Failed to update DC output %s", task_name)

        return success

    def is_output_active(self, task_name: str) -> bool:
        """
        Check if an output is currently active.

        Args:
            task_name: Name of the task

        Returns:
            True if output is active, False otherwise
        """
        return task_name in self._active_outputs

    def get_active_outputs(self) -> List[Dict[str, Any]]:
        """
        Get information about all active outputs.

        Returns:
            List of dictionaries with output info
        """
        result = []
        with self._lock:
            for task_name, config in self._active_outputs.items():
                result.append({
                    'task_name': task_name,
                    'channel': config.channel,
                    'signal_type': config.signal_type.value,
                    'output_mode': config.output_mode.value,
                    'waveform': config.waveform.value if config.signal_type == SignalType.AC else "DC",
                    'frequency': config.frequency,
                    'amplitude': config.amplitude,
                    'offset': config.offset,
                })
        return result

    def _generate_waveform(self,
                           waveform: WaveformType,
                           num_samples: int,
                           sample_rate: float,
                           frequency: float,
                           amplitude: float,
                           offset: float,
                           phase: float) -> np.ndarray:
        """
        Generate waveform data array.

        Args:
            waveform: Type of waveform to generate
            num_samples: Number of samples to generate
            sample_rate: Sample rate in Hz
            frequency: Signal frequency in Hz
            amplitude: Peak amplitude
            offset: DC offset
            phase: Phase offset in radians

        Returns:
            NumPy array of waveform samples
        """
        t = np.arange(num_samples) / sample_rate

        if waveform == WaveformType.CONSTANT:
            data = np.ones(num_samples) * amplitude

        elif waveform == WaveformType.SINE:
            data = amplitude * np.sin(2 * np.pi * frequency * t + phase)

        elif waveform == WaveformType.SQUARE:
            data = amplitude * np.sign(
                np.sin(2 * np.pi * frequency * t + phase)
            )

        elif waveform == WaveformType.TRIANGLE:
            # Triangle wave using sawtooth
            data = (2 * amplitude / np.pi) * np.arcsin(
                np.sin(2 * np.pi * frequency * t + phase)
            )

        elif waveform == WaveformType.SAWTOOTH:
            # Sawtooth wave
            period = 1.0 / frequency if frequency > 0 else 1.0
            data = 2 * amplitude * (
                (t / period + phase / (2 * np.pi)) % 1.0
            ) - amplitude

        else:
            data = np.zeros(num_samples)

        # Add offset
        data = data + offset

        return data

    def get_waveform_preview(self,
                              waveform: WaveformType,
                              num_samples: int = 1000,
                              sample_rate: float = 10000.0,
                              frequency: float = 60.0,
                              amplitude: float = 1.0,
                              offset: float = 0.0,
                              phase: float = 0.0) -> Tuple[np.ndarray, np.ndarray]:
        """
        Generate preview data for waveform visualization.

        Args:
            waveform: Type of waveform
            num_samples: Number of samples for preview
            sample_rate: Sample rate
            frequency: Signal frequency
            amplitude: Peak amplitude
            offset: DC offset
            phase: Phase in radians

        Returns:
            Tuple of (time_array, data_array)
        """
        t = np.arange(num_samples) / sample_rate
        data = self._generate_waveform(
            waveform, num_samples, sample_rate,
            frequency, amplitude, offset, phase
        )
        return t, data

    def cleanup(self) -> None:
        """Clean up all resources."""
        self.stop_all_outputs()
        log.info("AnalogOutputController cleaned up")