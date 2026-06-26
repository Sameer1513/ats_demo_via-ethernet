"""
Utility functions for NI DAQ Controller.

Provides helper functions for data conversion, string formatting,
channel parsing, and other common operations used across the application.

Typical usage:
    from utils import parse_channel_string, format_voltage
    channels = parse_channel_string("0:3")
    voltage_str = format_voltage(1.23456)
"""

import re
import csv
import io
import threading
import time
from typing import List, Optional, Tuple, Any, Dict, Callable
from datetime import datetime
from pathlib import Path
from logger import get_logger

log = get_logger(__name__)


def parse_channel_string(channel_str: str) -> List[str]:
    """
    Parse a channel string into a list of individual channel names.

    Supports various formats:
        - Single channel: "0" -> ["0"]
        - Range: "0:3" -> ["0", "1", "2", "3"]
        - List: "0,2,4" -> ["0", "2", "4"]
        - Mixed: "0:2,5,7:9" -> ["0","1","2","5","7","8","9"]
        - Named: "ai0:ai3" -> ["ai0","ai1","ai2","ai3"]

    Args:
        channel_str: Channel specification string

    Returns:
        List of individual channel identifiers
    """
    if not channel_str or not channel_str.strip():
        return []

    channel_str = channel_str.strip()
    channels: List[str] = []

    # Determine prefix if any (e.g., "ai", "ao", "port")
    prefix_match = re.match(r'^([a-zA-Z]+)', channel_str)
    prefix = prefix_match.group(1) if prefix_match else ''
    if prefix:
        # Remove prefix from string for processing
        working_str = channel_str[len(prefix):]
    else:
        working_str = channel_str

    # Split by comma
    parts = [p.strip() for p in working_str.split(',')]

    for part in parts:
        if not part:
            continue

        # Check for range (colon separated)
        range_match = re.match(r'^(\d+)\s*:\s*(\d+)$', part)
        if range_match:
            start = int(range_match.group(1))
            end = int(range_match.group(2))
            if start <= end:
                for i in range(start, end + 1):
                    channels.append(f"{prefix}{i}")
            else:
                for i in range(start, end - 1, -1):
                    channels.append(f"{prefix}{i}")
            continue

        # Check for single number
        num_match = re.match(r'^(\d+)$', part)
        if num_match:
            channels.append(f"{prefix}{num_match.group(1)}")
            continue

        # If it doesn't match any pattern, include as-is
        channels.append(f"{prefix}{part}")

    return channels


def format_voltage(value: float, precision: int = 4) -> str:
    """
    Format a voltage value for display.

    Args:
        value: Voltage value to format
        precision: Number of decimal places

    Returns:
        Formatted voltage string with units
    """
    return f"{value:.{precision}f} V"


def format_frequency(value: float) -> str:
    """
    Format a frequency value for display.

    Args:
        value: Frequency in Hz

    Returns:
        Formatted frequency string with appropriate unit
    """
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f} MHz"
    elif value >= 1_000:
        return f"{value / 1_000:.2f} kHz"
    else:
        return f"{value:.2f} Hz"


def format_time(seconds: float) -> str:
    """
    Format a time duration for display.

    Args:
        seconds: Time in seconds

    Returns:
        Formatted time string
    """
    if seconds < 0.001:
        return f"{seconds * 1_000_000:.0f} µs"
    elif seconds < 1.0:
        return f"{seconds * 1_000:.1f} ms"
    elif seconds < 60:
        return f"{seconds:.2f} s"
    elif seconds < 3600:
        minutes = int(seconds // 60)
        secs = seconds % 60
        return f"{minutes}m {secs:.1f}s"
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours}h {minutes}m"


def validate_numeric_input(value_str: str,
                           min_val: Optional[float] = None,
                           max_val: Optional[float] = None) -> Tuple[bool, Optional[float], str]:
    """
    Validate and parse a numeric input string.

    Args:
        value_str: Input string to validate
        min_val: Minimum allowed value (optional)
        max_val: Maximum allowed value (optional)

    Returns:
        Tuple of (is_valid, parsed_value, error_message)
    """
    if not value_str or not value_str.strip():
        return False, None, "Value cannot be empty"

    try:
        value = float(value_str.strip())
    except ValueError:
        return False, None, f"Invalid number: '{value_str}'"

    if min_val is not None and value < min_val:
        return False, None, f"Value {value} is below minimum {min_val}"

    if max_val is not None and value > max_val:
        return False, None, f"Value {value} is above maximum {max_val}"

    return True, value, ""


def safe_float_convert(value: Any, default: float = 0.0) -> float:
    """
    Safely convert a value to float.

    Args:
        value: Value to convert
        default: Default value if conversion fails

    Returns:
        Float value or default
    """
    if value is None:
        return default

    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def channels_to_nidaqmx_string(channels: List[str]) -> str:
    """
    Convert a list of channels to a NI-DAQmx channel string.

    Args:
        channels: List of channel identifiers

    Returns:
        Comma-separated channel string for NI-DAQmx
    """
    if not channels:
        return ""

    return ",".join(channels)


def group_channels_by_type(channels: List[str]) -> Dict[str, List[str]]:
    """
    Group channels by their type prefix (ai, ao, di, do, etc.).

    Args:
        channels: List of channel names

    Returns:
        Dictionary mapping channel type to list of channels
    """
    groups: Dict[str, List[str]] = {}

    for channel in channels:
        # Extract prefix (letters before first digit)
        match = re.match(r'^([a-zA-Z]+)', channel)
        prefix = match.group(1).lower() if match else 'unknown'

        if prefix not in groups:
            groups[prefix] = []
        groups[prefix].append(channel)

    return groups


class MovingAverage:
    """
    Simple moving average calculator for smoothing data.

    Maintains a fixed-size buffer of values and computes the running average.

    Attributes:
        window_size: Number of samples in the moving window
        _buffer: Circular buffer of values
        _index: Current position in buffer
        _count: Number of samples collected
    """

    def __init__(self, window_size: int = 10) -> None:
        """
        Initialize moving average calculator.

        Args:
            window_size: Number of samples to average (default: 10)
        """
        self.window_size = max(1, window_size)
        self._buffer: List[float] = [0.0] * self.window_size
        self._index = 0
        self._count = 0
        self._lock = threading.Lock()

    def add_sample(self, value: float) -> float:
        """
        Add a new sample and return the current average.

        Args:
            value: New sample value

        Returns:
            Current moving average
        """
        with self._lock:
            self._buffer[self._index] = value
            self._index = (self._index + 1) % self.window_size
            self._count = min(self._count + 1, self.window_size)
            return self.get_average()

    def get_average(self) -> float:
        """
        Get the current moving average.

        Returns:
            Current average value
        """
        if self._count == 0:
            return 0.0
        return sum(self._buffer[:self._count]) / self._count

    def reset(self) -> None:
        """Reset the moving average buffer."""
        with self._lock:
            self._buffer = [0.0] * self.window_size
            self._index = 0
            self._count = 0


class TimedLoop:
    """
    Utility for running a loop at a fixed rate.

    Provides accurate timing for periodic operations with
    compensation for execution time.

    Attributes:
        interval: Target interval between iterations in seconds
        _last_time: Timestamp of last iteration
        _running: Whether the loop is active
    """

    def __init__(self, interval_ms: float = 100.0) -> None:
        """
        Initialize timed loop.

        Args:
            interval_ms: Target interval in milliseconds
        """
        self.interval = interval_ms / 1000.0
        self._last_time = time.perf_counter()
        self._running = False

    def start(self) -> None:
        """Start the timed loop timing."""
        self._last_time = time.perf_counter()
        self._running = True

    def stop(self) -> None:
        """Stop the timed loop."""
        self._running = False

    def wait_for_next(self) -> float:
        """
        Wait until the next interval elapses.

        Returns:
            Actual time elapsed since last iteration in seconds

        Raises:
            RuntimeError: If loop is not running
        """
        if not self._running:
            raise RuntimeError("TimedLoop is not running")

        current_time = time.perf_counter()
        elapsed = current_time - self._last_time
        sleep_time = self.interval - elapsed

        if sleep_time > 0:
            time.sleep(sleep_time)
            actual_elapsed = time.perf_counter() - self._last_time
        else:
            actual_elapsed = elapsed

        self._last_time = time.perf_counter()
        return actual_elapsed


def export_to_csv(filepath: str,
                  data: List[Dict[str, Any]],
                  fieldnames: Optional[List[str]] = None) -> bool:
    """
    Export data to a CSV file.

    Args:
        filepath: Path to output CSV file
        data: List of dictionaries (rows) to export
        fieldnames: Column headers. If None, inferred from first row.

    Returns:
        True if export succeeded, False otherwise
    """
    try:
        if not data:
            log.warning("No data to export to CSV: %s", filepath)
            return False

        if fieldnames is None:
            fieldnames = list(data[0].keys())

        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, 'w', newline='') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(data)

        log.info("Data exported to CSV: %s (%d rows)", filepath, len(data))
        return True

    except (IOError, csv.Error) as e:
        log.error("Failed to export CSV to %s: %s", filepath, e)
        return False


def get_timestamp_string() -> str:
    """
    Get a formatted timestamp string for filenames.

    Returns:
        Timestamp string in format 'YYYYMMDD_HHMMSS'
    """
    return datetime.now().strftime('%Y%m%d_%H%M%S')


def generate_output_filename(prefix: str = 'daq_data',
                             extension: str = '.csv') -> str:
    """
    Generate a unique output filename with timestamp.

    Args:
        prefix: Filename prefix
        extension: File extension

    Returns:
        Unique filename string
    """
    timestamp = get_timestamp_string()
    return f"{prefix}_{timestamp}{extension}"