"""
Analog Input module for NI DAQ Controller.

Provides high-level operations for analog input acquisition including
single-sample reads, finite multi-sample reads, and continuous acquisition
with live plotting and CSV export capabilities.

Features:
    - Single sample read
    - Finite multi-sample acquisition
    - Continuous background acquisition
    - Configurable sample rate and sample count
    - Channel selection (single or multiple)
    - Voltage range configuration
    - Live data visualization
    - CSV data export
    - Thread-safe operations

Typical usage:
    from analog_input import AnalogInputController
    ai = AnalogInputController(task_manager, module_info)
    task_name = ai.start_single_acquisition(["ai0", "ai1"])
    data = ai.read_data(task_name)
"""

import time
import threading
import numpy as np
from typing import List, Optional, Tuple, Dict, Any, Callable
from dataclasses import dataclass, field
from pathlib import Path
from logger import get_logger
from task_manager import TaskManager, TaskType
from device_manager import ModuleInfo
from utils import export_to_csv, generate_output_filename, MovingAverage

log = get_logger(__name__)


@dataclass
class AcquisitionConfig:
    """
    Configuration for an analog input acquisition.

    Attributes:
        channels: List of channel names to acquire from
        sample_rate: Sampling rate in Hz
        num_samples: Number of samples per channel per read
        voltage_min: Minimum voltage range
        voltage_max: Maximum voltage range
        terminal_config: Terminal configuration (RSE, NRSE, Differential)
        continuous: Whether this is a continuous acquisition
    """
    channels: List[str]
    sample_rate: float = 1000.0
    num_samples: int = 100
    voltage_min: float = -10.0
    voltage_max: float = 10.0
    terminal_config: str = "RSE"
    continuous: bool = False


@dataclass
class AcquisitionResult:
    """
    Result of an analog input acquisition.

    Attributes:
        task_name: Name of the task used for acquisition
        time_stamps: Array of timestamps for each sample
        data: Dictionary mapping channel names to data arrays
        sample_rate: Sample rate used
        channel_count: Number of channels acquired
        success: Whether the acquisition succeeded
        error_message: Error message if failed
    """
    task_name: str = ""
    time_stamps: np.ndarray = field(default_factory=lambda: np.array([]))
    data: Dict[str, np.ndarray] = field(default_factory=dict)
    sample_rate: float = 0.0
    channel_count: int = 0
    success: bool = False
    error_message: str = ""


class AnalogInputController:
    """
    Controller for analog input operations.

    Manages acquisition tasks, data buffering, and provides both
    single-shot and continuous acquisition modes with thread-safe
    data access.

    Attributes:
        task_manager: Reference to the global TaskManager
        module_info: Module information for the device
        _active_acquisitions: Tracking active acquisition tasks
        _data_buffers: Buffers for continuous acquisition data
        _lock: Thread lock for safe concurrent access
        _monitoring_callbacks: Callbacks for live data updates
    """

    def __init__(self, task_manager: TaskManager,
                 module_info: ModuleInfo) -> None:
        """
        Initialize analog input controller.

        Args:
            task_manager: Global TaskManager instance
            module_info: Module information for the device
        """
        self.task_manager = task_manager
        self.module_info = module_info
        self.device_name = module_info.name

        self._active_acquisitions: Dict[str, AcquisitionConfig] = {}
        self._data_buffers: Dict[str, List[np.ndarray]] = {}
        self._lock = threading.Lock()
        self._monitoring_callbacks: Dict[str, Callable] = {}

        log.info(
            "AnalogInputController initialized for %s "
            "(%d AI channels)",
            self.device_name, len(module_info.ai_channels)
        )

    def get_available_channels(self) -> List[str]:
        """
        Get available analog input channels.

        Returns:
            List of available analog input channel names
        """
        return list(self.module_info.ai_channels)

    def get_max_sample_rate(self) -> float:
        """
        Get maximum supported sample rate.

        Returns:
            Maximum sample rate in Hz
        """
        return self.module_info.max_sample_rate

    def get_voltage_ranges(self) -> List[Tuple[float, float]]:
        """
        Get supported voltage ranges.

        Returns:
            List of (min, max) voltage range tuples
        """
        if self.module_info.voltage_ranges:
            return self.module_info.voltage_ranges
        return [(-10.0, 10.0)]

    def start_single_acquisition(self,
                                  channels: List[str],
                                  sample_rate: float = 1000.0,
                                  num_samples: int = 100,
                                  voltage_min: float = -10.0,
                                  voltage_max: float = 10.0,
                                  terminal_config: str = "RSE") -> Optional[str]:
        """
        Start a single (finite) analog input acquisition.

        Args:
            channels: List of channels to read from
            sample_rate: Sampling rate in Hz
            num_samples: Number of samples per channel
            voltage_min: Minimum voltage
            voltage_max: Maximum voltage
            terminal_config: Terminal configuration

        Returns:
            Task name if successful, None otherwise
        """
        # Validate channels
        valid_channels = self._validate_channels(channels)
        if not valid_channels:
            log.error("No valid channels specified for acquisition")
            return None

        # Create task
        task_name = self.task_manager.create_ai_task(
            device_name=self.device_name,
            channels=valid_channels,
            sample_rate=sample_rate,
            num_samples=num_samples,
            voltage_range=(voltage_min, voltage_max),
            terminal_config=terminal_config
        )

        if task_name is None:
            log.error("Failed to create AI task for %s", self.device_name)
            return None

        # Store acquisition config
        config = AcquisitionConfig(
            channels=valid_channels,
            sample_rate=sample_rate,
            num_samples=num_samples,
            voltage_min=voltage_min,
            voltage_max=voltage_max,
            terminal_config=terminal_config,
            continuous=False
        )

        with self._lock:
            self._active_acquisitions[task_name] = config

        log.info(
            "Started single acquisition: task=%s, channels=%s, "
            "rate=%.1f Hz, samples=%d",
            task_name, valid_channels, sample_rate, num_samples
        )

        return task_name

    def read_data(self, task_name: str) -> Optional[AcquisitionResult]:
        """
        Read data from an acquisition task.

        Reads the configured number of samples and returns structured
        results with timestamps and per-channel data.

        Args:
            task_name: Name of the task to read from

        Returns:
            AcquisitionResult with data if successful, None otherwise
        """
        config = self._active_acquisitions.get(task_name)
        if config is None:
            log.error("Unknown acquisition task: %s", task_name)
            return None

        # Read raw data
        raw_data = self.task_manager.read_analog(task_name)
        if raw_data is None:
            return None

        # Process into per-channel data
        return self._process_read_data(raw_data, config, task_name)

    def read_single_sample(self, channels: List[str],
                           voltage_min: float = -10.0,
                           voltage_max: float = 10.0) -> Optional[Dict[str, float]]:
        """
        Read a single sample from specified channels.

        Convenience method for one-shot readings.

        Args:
            channels: List of channels to read
            voltage_min: Minimum voltage
            voltage_max: Maximum voltage

        Returns:
            Dictionary mapping channel names to values, or None on failure
        """
        valid_channels = self._validate_channels(channels)
        if not valid_channels:
            return None

        # Create a single-sample task
        task_name = self.task_manager.create_ai_task(
            device_name=self.device_name,
            channels=valid_channels,
            sample_rate=1000.0,
            num_samples=1,
            voltage_range=(voltage_min, voltage_max)
        )

        if task_name is None:
            return None

        try:
            # Start and read
            self.task_manager.start_task(task_name)
            data = self.task_manager.read_analog(task_name)

            if data is None:
                return None

            # Map to channels
            result = {}
            for i, channel in enumerate(valid_channels):
                if i < len(data):
                    result[channel] = float(data[i])

            return result

        finally:
            # Clean up
            self.task_manager.clear_task(task_name)

    def start_continuous_acquisition(self,
                                      channels: List[str],
                                      sample_rate: float = 1000.0,
                                      num_samples: int = 100,
                                      voltage_min: float = -10.0,
                                      voltage_max: float = 10.0,
                                      terminal_config: str = "RSE",
                                      data_callback: Optional[Callable] = None) -> Optional[str]:
        """
        Start continuous analog input acquisition.

        Runs in a background thread, calling the provided callback with
        each new data set.

        Args:
            channels: List of channels to read from
            sample_rate: Sampling rate in Hz
            num_samples: Samples per channel per read
            voltage_min: Minimum voltage
            voltage_max: Maximum voltage
            terminal_config: Terminal configuration
            data_callback: Callback function for each data read

        Returns:
            Task name if successful, None otherwise
        """
        valid_channels = self._validate_channels(channels)
        if not valid_channels:
            return None

        # Create task
        task_name = self.task_manager.create_ai_task(
            device_name=self.device_name,
            channels=valid_channels,
            sample_rate=sample_rate,
            num_samples=num_samples,
            voltage_range=(voltage_min, voltage_max),
            terminal_config=terminal_config
        )

        if task_name is None:
            return None

        # Store config
        config = AcquisitionConfig(
            channels=valid_channels,
            sample_rate=sample_rate,
            num_samples=num_samples,
            voltage_min=voltage_min,
            voltage_max=voltage_max,
            terminal_config=terminal_config,
            continuous=True
        )

        with self._lock:
            self._active_acquisitions[task_name] = config
            self._data_buffers[task_name] = []

        # Register callback
        if data_callback is not None:
            self._monitoring_callbacks[task_name] = data_callback

            # Wrap callback to include data processing
            def wrapped_callback(data: np.ndarray) -> None:
                """Wrapper that processes data before calling user callback."""
                result = self._process_read_data(
                    data, config, task_name
                )
                if result and result.success:
                    # Buffer data
                    with self._lock:
                        if task_name in self._data_buffers:
                            self._data_buffers[task_name].append(data)

                    # Call user callback
                    try:
                        data_callback(result)
                    except Exception as e:
                        log.error("Data callback error: %s", e)

            # Start background acquisition
            self.task_manager.start_background_acquisition(
                task_name,
                wrapped_callback
            )

            log.info(
                "Started continuous acquisition: task=%s, channels=%s, "
                "rate=%.1f Hz",
                task_name, valid_channels, sample_rate
            )

        return task_name

    def stop_acquisition(self, task_name: str) -> bool:
        """
        Stop an active acquisition.

        Args:
            task_name: Name of the task to stop

        Returns:
            True if stopped successfully, False otherwise
        """
        # Stop background acquisition if running
        self.task_manager.stop_background_acquisition(task_name)

        # Stop and clear the task
        self.task_manager.stop_task(task_name)
        self.task_manager.clear_task(task_name)

        # Clean up local state
        with self._lock:
            self._active_acquisitions.pop(task_name, None)
            self._data_buffers.pop(task_name, None)
            self._monitoring_callbacks.pop(task_name, None)

        log.info("Stopped acquisition: %s", task_name)
        return True

    def stop_all_acquisitions(self) -> None:
        """Stop all active acquisitions."""
        with self._lock:
            task_names = list(self._active_acquisitions.keys())

        for task_name in task_names:
            self.stop_acquisition(task_name)

        log.info("All acquisitions stopped")

    def export_data_to_csv(self, task_name: str,
                           filepath: Optional[str] = None) -> Optional[str]:
        """
        Export acquired data to a CSV file.

        Args:
            task_name: Name of the task
            filepath: Output file path. Auto-generated if None.

        Returns:
            Path to saved file if successful, None otherwise
        """
        config = self._active_acquisitions.get(task_name)
        if config is None:
            log.error("No data found for task: %s", task_name)
            return None

        # Get data from buffer or last read
        with self._lock:
            buffer_data = self._data_buffers.get(task_name, [])

        if not buffer_data:
            log.warning("No buffered data for task: %s", task_name)
            return None

        # Concatenate buffer data
        all_data = np.concatenate(buffer_data, axis=1)

        # Build CSV data
        csv_data = []
        num_samples = all_data.shape[1] if all_data.ndim > 1 else 1
        time_per_sample = 1.0 / config.sample_rate

        for i in range(num_samples):
            row = {
                'Sample': i,
                'Time_s': i * time_per_sample,
            }

            if all_data.ndim > 1:
                for j, channel in enumerate(config.channels):
                    if j < all_data.shape[0]:
                        row[f'{channel}_V'] = float(all_data[j, i])
            else:
                row[f'{config.channels[0]}_V'] = float(all_data[i])

            csv_data.append(row)

        # Generate filename if not provided
        if filepath is None:
            filepath = generate_output_filename(
                prefix=f'ai_{self.device_name}',
                extension='.csv'
            )

        # Export
        if export_to_csv(filepath, csv_data):
            log.info("Data exported to CSV: %s (%d samples)",
                     filepath, num_samples)
            return filepath

        return None

    def get_buffer_size(self, task_name: str) -> int:
        """
        Get number of samples currently in the data buffer.

        Args:
            task_name: Name of the task

        Returns:
            Number of buffered samples
        """
        with self._lock:
            buffer_data = self._data_buffers.get(task_name, [])
            if buffer_data:
                return sum(d.shape[1] if d.ndim > 1 else 1 for d in buffer_data)
            return 0

    def clear_buffer(self, task_name: str) -> None:
        """
        Clear the data buffer for a task.

        Args:
            task_name: Name of the task
        """
        with self._lock:
            if task_name in self._data_buffers:
                self._data_buffers[task_name] = []
                log.debug("Cleared buffer for task: %s", task_name)

    def is_acquisition_active(self, task_name: str) -> bool:
        """
        Check if an acquisition is currently active.

        Args:
            task_name: Name of the task

        Returns:
            True if acquisition is active, False otherwise
        """
        return task_name in self._active_acquisitions

    def get_active_acquisitions(self) -> List[Dict[str, Any]]:
        """
        Get information about all active acquisitions.

        Returns:
            List of dictionaries with acquisition info
        """
        result = []
        with self._lock:
            for task_name, config in self._active_acquisitions.items():
                result.append({
                    'task_name': task_name,
                    'channels': config.channels,
                    'sample_rate': config.sample_rate,
                    'num_samples': config.num_samples,
                    'continuous': config.continuous,
                    'buffer_size': self.get_buffer_size(task_name)
                })
        return result

    def _validate_channels(self, channels: List[str]) -> List[str]:
        """
        Validate that requested channels are available.

        Args:
            channels: List of channel names to validate

        Returns:
            List of valid channel names
        """
        available = set(self.module_info.ai_channels)
        valid = [ch for ch in channels if ch in available]

        if len(valid) != len(channels):
            invalid = set(channels) - available
            log.warning("Invalid channels (ignored): %s", invalid)

        return valid

    def _process_read_data(self,
                           raw_data: np.ndarray,
                           config: AcquisitionConfig,
                           task_name: str) -> AcquisitionResult:
        """
        Process raw NI-DAQmx data into structured per-channel format.

        Args:
            raw_data: Raw data array from NI-DAQmx
            config: Acquisition configuration
            task_name: Name of the task

        Returns:
            AcquisitionResult with processed data
        """
        try:
            num_channels = len(config.channels)

            # Generate timestamps
            num_samples = raw_data.shape[0] if raw_data.ndim > 0 else 1
            time_per_sample = 1.0 / config.sample_rate
            time_stamps = np.arange(num_samples) * time_per_sample

            # Organize data by channel
            channel_data: Dict[str, np.ndarray] = {}

            if raw_data.ndim == 2:
                # Multi-channel data: rows = channels, cols = samples
                for i, channel in enumerate(config.channels):
                    if i < raw_data.shape[0]:
                        channel_data[channel] = raw_data[i, :]
                    else:
                        channel_data[channel] = np.array([])
            else:
                # Single channel
                if config.channels:
                    channel_data[config.channels[0]] = raw_data

            return AcquisitionResult(
                task_name=task_name,
                time_stamps=time_stamps,
                data=channel_data,
                sample_rate=config.sample_rate,
                channel_count=num_channels,
                success=True
            )

        except Exception as e:
            log.error("Failed to process read data: %s", e)
            return AcquisitionResult(
                task_name=task_name,
                success=False,
                error_message=str(e)
            )

    def cleanup(self) -> None:
        """Clean up all resources."""
        self.stop_all_acquisitions()
        log.info("AnalogInputController cleaned up")