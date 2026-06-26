"""
Task Manager module for NI DAQ Controller.

Manages NI-DAQmx tasks for analog input, analog output, and digital I/O operations.
Provides a thread-safe interface for creating, starting, stopping, and managing
DAQ tasks with proper resource cleanup.

Each DAQ operation is wrapped in a Task object that handles:
    - Task creation and configuration
    - Channel configuration
    - Timing configuration
    - Read/Write operations
    - Error handling and cleanup

Typical usage:
    from task_manager import TaskManager
    tm = TaskManager()
    task = tm.create_ai_task("cDAQ1Mod1", ["ai0", "ai1"], 1000.0, 100)
    data = tm.read_analog(task)
    tm.stop_task(task)
"""

import time
import threading
import numpy as np
from typing import List, Optional, Tuple, Dict, Any, Callable
from enum import Enum
from dataclasses import dataclass, field
from logger import get_logger

log = get_logger(__name__)


class TaskType(Enum):
    """Types of DAQ tasks supported by the application."""
    ANALOG_INPUT = "analog_input"
    ANALOG_OUTPUT = "analog_output"
    DIGITAL_INPUT = "digital_input"
    DIGITAL_OUTPUT = "digital_output"
    COUNTER_INPUT = "counter_input"
    COUNTER_OUTPUT = "counter_output"


class TaskState(Enum):
    """States a DAQ task can be in during its lifecycle."""
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"
    COMPLETED = "completed"


@dataclass
class TaskInfo:
    """
    Information about a DAQ task.

    Attributes:
        name: Unique task identifier
        device_name: Name of the NI DAQ device
        channels: List of channel names
        task_type: Type of task (AI, AO, DI, DO, etc.)
        state: Current task state
        sample_rate: Sampling rate in Hz
        num_samples: Number of samples per channel
        created_at: Timestamp of task creation
        error_message: Last error message if in ERROR state
    """
    name: str
    device_name: str
    channels: List[str]
    task_type: TaskType
    state: TaskState = TaskState.IDLE
    sample_rate: float = 1000.0
    num_samples: int = 100
    created_at: float = field(default_factory=time.time)
    error_message: str = ""


class TaskManager:
    """
    Thread-safe manager for NI-DAQmx tasks.

    Handles creation, configuration, execution, and cleanup of all DAQ tasks.
    Provides methods for analog I/O, digital I/O operations with proper
    resource management.

    Attributes:
        _tasks: Dictionary mapping task names to TaskInfo
        _nidaqmx_tasks: Dictionary mapping task names to nidaqmx Task objects
        _lock: Thread lock for safe concurrent access
        _running: Dictionary tracking running background threads
        _data_callbacks: Callbacks for continuous data acquisition
    """

    def __init__(self) -> None:
        """Initialize the task manager."""
        self._tasks: Dict[str, TaskInfo] = {}
        self._nidaqmx_tasks: Dict[str, Any] = {}
        self._lock = threading.Lock()
        self._running: Dict[str, threading.Event] = {}
        self._data_callbacks: Dict[str, Callable] = {}
        self._background_threads: Dict[str, threading.Thread] = {}
        self._initialized = False

        log.info("TaskManager initialized")

    def _check_nidaqmx(self) -> bool:
        """
        Check if nidaqmx is available and initialize if needed.

        Returns:
            True if nidaqmx is available, False otherwise
        """
        if self._initialized:
            return True

        try:
            import nidaqmx
            self._nidaqmx = nidaqmx
            self._initialized = True
            log.debug("NI-DAQmx library loaded successfully")
            return True
        except ImportError:
            log.error(
                "NI-DAQmx library is not installed. "
                "Install with: pip install nidaqmx"
            )
            return False
        except Exception as e:
            log.error("Failed to load NI-DAQmx library: %s", e)
            return False

    def _generate_task_name(self, device_name: str,
                            task_type: TaskType) -> str:
        """
        Generate a unique task name.

        Args:
            device_name: Name of the DAQ device
            task_type: Type of task

        Returns:
            Unique task name string
        """
        timestamp = int(time.time() * 1000)
        return f"{device_name}_{task_type.value}_{timestamp}"

    def create_ai_task(self,
                       device_name: str,
                       channels: List[str],
                       sample_rate: float = 1000.0,
                       num_samples: int = 100,
                       voltage_range: Tuple[float, float] = (-10.0, 10.0),
                       terminal_config: str = "RSE") -> Optional[str]:
        """
        Create an analog input task.

        Args:
            device_name: Name of the DAQ device (e.g., "cDAQ1Mod1")
            channels: List of analog input channels (e.g., ["ai0", "ai1"])
            sample_rate: Sampling rate in Hz
            num_samples: Number of samples per channel
            voltage_range: Min/max voltage range tuple
            terminal_config: Terminal configuration ("RSE", "NRSE", "Differential")

        Returns:
            Task name string if successful, None otherwise
        """
        if not self._check_nidaqmx():
            return None

        task_name = self._generate_task_name(device_name, TaskType.ANALOG_INPUT)

        try:
            task = self._nidaqmx.Task(task_name)

            # Add analog input channels
            channel_string = ",".join(
                [f"{device_name}/{ch}" for ch in channels]
            )

            task.ai_channels.add_ai_voltage_chan(
                channel_string,
                terminal_config=getattr(
                    self._nidaqmx.constants.TerminalConfiguration,
                    terminal_config
                ),
                min_val=voltage_range[0],
                max_val=voltage_range[1]
            )

            # Configure timing
            if num_samples > 1:
                task.timing.cfg_samp_clk_timing(
                    rate=sample_rate,
                    sample_mode=self._nidaqmx.constants.AcquisitionType.FINITE,
                    samps_per_chan=num_samples
                )
            else:
                # Single sample - no timing needed
                pass

            # Store task info
            task_info = TaskInfo(
                name=task_name,
                device_name=device_name,
                channels=channels,
                task_type=TaskType.ANALOG_INPUT,
                sample_rate=sample_rate,
                num_samples=num_samples
            )

            with self._lock:
                self._tasks[task_name] = task_info
                self._nidaqmx_tasks[task_name] = task

            log.info(
                "Created AI task '%s' on %s, channels=%s, rate=%.1f Hz, samples=%d",
                task_name, device_name, channels, sample_rate, num_samples
            )

            return task_name

        except Exception as e:
            log.error("Failed to create AI task: %s", e)
            self._cleanup_task(task_name)
            return None

    def create_ao_task(self,
                       device_name: str,
                       channels: List[str],
                       sample_rate: float = 1000.0,
                       voltage_range: Tuple[float, float] = (-10.0, 10.0)) -> Optional[str]:
        """
        Create an analog output task.

        Args:
            device_name: Name of the DAQ device
            channels: List of analog output channels
            sample_rate: Update rate in Hz
            voltage_range: Min/max voltage range tuple

        Returns:
            Task name string if successful, None otherwise
        """
        if not self._check_nidaqmx():
            return None

        task_name = self._generate_task_name(device_name, TaskType.ANALOG_OUTPUT)

        try:
            task = self._nidaqmx.Task(task_name)

            # Add analog output channels
            channel_string = ",".join(
                [f"{device_name}/{ch}" for ch in channels]
            )

            task.ao_channels.add_ao_voltage_chan(
                channel_string,
                min_val=voltage_range[0],
                max_val=voltage_range[1]
            )

            # Store task info
            task_info = TaskInfo(
                name=task_name,
                device_name=device_name,
                channels=channels,
                task_type=TaskType.ANALOG_OUTPUT,
                sample_rate=sample_rate
            )

            with self._lock:
                self._tasks[task_name] = task_info
                self._nidaqmx_tasks[task_name] = task

            log.info(
                "Created AO task '%s' on %s, channels=%s",
                task_name, device_name, channels
            )

            return task_name

        except Exception as e:
            log.error("Failed to create AO task: %s", e)
            self._cleanup_task(task_name)
            return None

    def create_di_task(self,
                       device_name: str,
                       channels: List[str]) -> Optional[str]:
        """
        Create a digital input task.

        Args:
            device_name: Name of the DAQ device
            channels: List of digital input channels/lines

        Returns:
            Task name string if successful, None otherwise
        """
        if not self._check_nidaqmx():
            return None

        task_name = self._generate_task_name(device_name, TaskType.DIGITAL_INPUT)

        try:
            task = self._nidaqmx.Task(task_name)

            # Add digital input channels
            channel_string = ",".join(
                [f"{device_name}/{ch}" for ch in channels]
            )

            task.di_channels.add_di_chan(channel_string)

            task_info = TaskInfo(
                name=task_name,
                device_name=device_name,
                channels=channels,
                task_type=TaskType.DIGITAL_INPUT
            )

            with self._lock:
                self._tasks[task_name] = task_info
                self._nidaqmx_tasks[task_name] = task

            log.info(
                "Created DI task '%s' on %s, channels=%s",
                task_name, device_name, channels
            )

            return task_name

        except Exception as e:
            log.error("Failed to create DI task: %s", e)
            self._cleanup_task(task_name)
            return None

    def create_do_task(self,
                       device_name: str,
                       channels: List[str]) -> Optional[str]:
        """
        Create a digital output task.

        Args:
            device_name: Name of the DAQ device
            channels: List of digital output channels/lines

        Returns:
            Task name string if successful, None otherwise
        """
        if not self._check_nidaqmx():
            return None

        task_name = self._generate_task_name(device_name, TaskType.DIGITAL_OUTPUT)

        try:
            task = self._nidaqmx.Task(task_name)

            channel_string = ",".join(
                [f"{device_name}/{ch}" for ch in channels]
            )

            task.do_channels.add_do_chan(channel_string)

            task_info = TaskInfo(
                name=task_name,
                device_name=device_name,
                channels=channels,
                task_type=TaskType.DIGITAL_OUTPUT
            )

            with self._lock:
                self._tasks[task_name] = task_info
                self._nidaqmx_tasks[task_name] = task

            log.info(
                "Created DO task '%s' on %s, channels=%s",
                task_name, device_name, channels
            )

            return task_name

        except Exception as e:
            log.error("Failed to create DO task: %s", e)
            self._cleanup_task(task_name)
            return None

    def read_analog(self, task_name: str,
                    timeout: float = 10.0) -> Optional[np.ndarray]:
        """
        Read analog data from a task.

        Args:
            task_name: Name of the task to read from
            timeout: Timeout in seconds for the read operation

        Returns:
            NumPy array of data if successful, None otherwise
        """
        if not self._check_nidaqmx():
            return None

        with self._lock:
            task_info = self._tasks.get(task_name)
            task = self._nidaqmx_tasks.get(task_name)

            if task_info is None or task is None:
                log.error("Task '%s' not found", task_name)
                return None

        try:
            if task_info.num_samples > 1:
                data = task.read(
                    number_of_samples_per_channel=task_info.num_samples,
                    timeout=timeout
                )
                return np.array(data)
            else:
                data = task.read(number_of_samples_per_channel=1, timeout=timeout)
                return np.array([data])

        except Exception as e:
            log.error("Failed to read from task '%s': %s", task_name, e)
            self._update_task_state(task_name, TaskState.ERROR, str(e))
            return None

    def write_analog(self, task_name: str,
                     data: np.ndarray,
                     timeout: float = 10.0,
                     auto_start: bool = True) -> bool:
        """
        Write analog data to a task.

        Args:
            task_name: Name of the task to write to
            data: Data array to write
            timeout: Timeout in seconds
            auto_start: Whether to auto-start the task

        Returns:
            True if write succeeded, False otherwise
        """
        if not self._check_nidaqmx():
            return False

        with self._lock:
            task = self._nidaqmx_tasks.get(task_name)

            if task is None:
                log.error("Task '%s' not found", task_name)
                return False

        try:
            # Ensure data is 2D for multi-channel writes
            if data.ndim == 1:
                data = data.reshape(1, -1)

            samples_written = task.write(
                data,
                timeout=timeout,
                auto_start=auto_start
            )

            log.debug(
                "Wrote %d samples to task '%s'",
                samples_written, task_name
            )

            return True

        except Exception as e:
            log.error("Failed to write to task '%s': %s", task_name, e)
            self._update_task_state(task_name, TaskState.ERROR, str(e))
            return False

    def read_digital(self, task_name: str,
                     timeout: float = 10.0) -> Optional[List[int]]:
        """
        Read digital data from a task.

        Args:
            task_name: Name of the task to read from
            timeout: Timeout in seconds

        Returns:
            List of digital values if successful, None otherwise
        """
        if not self._check_nidaqmx():
            return None

        with self._lock:
            task = self._nidaqmx_tasks.get(task_name)

            if task is None:
                log.error("Task '%s' not found", task_name)
                return None

        try:
            data = task.read(number_of_samples_per_channel=1, timeout=timeout)
            return [data] if not isinstance(data, list) else data

        except Exception as e:
            log.error("Failed to read digital from '%s': %s", task_name, e)
            self._update_task_state(task_name, TaskState.ERROR, str(e))
            return None

    def write_digital(self, task_name: str,
                      values: List[bool],
                      auto_start: bool = True) -> bool:
        """
        Write digital values to a task.

        Args:
            task_name: Name of the task to write to
            values: List of boolean values to write
            auto_start: Whether to auto-start the task

        Returns:
            True if write succeeded, False otherwise
        """
        if not self._check_nidaqmx():
            return False

        with self._lock:
            task = self._nidaqmx_tasks.get(task_name)

            if task is None:
                log.error("Task '%s' not found", task_name)
                return False

        try:
            task.write(values, auto_start=auto_start)
            log.debug("Wrote digital values to task '%s'", task_name)
            return True

        except Exception as e:
            log.error("Failed to write digital to '%s': %s", task_name, e)
            self._update_task_state(task_name, TaskState.ERROR, str(e))
            return False

    def start_task(self, task_name: str) -> bool:
        """
        Start a DAQ task.

        Args:
            task_name: Name of the task to start

        Returns:
            True if started successfully, False otherwise
        """
        if not self._check_nidaqmx():
            return False

        with self._lock:
            task = self._nidaqmx_tasks.get(task_name)

            if task is None:
                log.error("Task '%s' not found", task_name)
                return False

        try:
            task.start()
            self._update_task_state(task_name, TaskState.RUNNING)
            log.info("Started task '%s'", task_name)
            return True

        except Exception as e:
            log.error("Failed to start task '%s': %s", task_name, e)
            self._update_task_state(task_name, TaskState.ERROR, str(e))
            return False

    def stop_task(self, task_name: str) -> bool:
        """
        Stop a running DAQ task.

        Args:
            task_name: Name of the task to stop

        Returns:
            True if stopped successfully, False otherwise
        """
        if not self._check_nidaqmx():
            return False

        with self._lock:
            task = self._nidaqmx_tasks.get(task_name)

            if task is None:
                return False

        try:
            task.stop()
            self._update_task_state(task_name, TaskState.IDLE)
            log.info("Stopped task '%s'", task_name)
            return True

        except Exception as e:
            log.error("Failed to stop task '%s': %s", task_name, e)
            return False

    def clear_task(self, task_name: str) -> bool:
        """
        Clear (destroy) a DAQ task and free its resources.

        Args:
            task_name: Name of the task to clear

        Returns:
            True if cleared successfully, False otherwise
        """
        # Stop background thread if running
        self.stop_background_acquisition(task_name)

        with self._lock:
            task_info = self._tasks.pop(task_name, None)

            try:
                task = self._nidaqmx_tasks.pop(task_name, None)
                if task is not None:
                    try:
                        task.stop()
                    except Exception:
                        pass
                    try:
                        task.close()
                    except Exception as e:
                        log.warning("Error closing task '%s': %s", task_name, e)

                log.info("Cleared task '%s'", task_name)
                return True

            except Exception as e:
                log.error("Failed to clear task '%s': %s", task_name, e)
                return False

    def start_background_acquisition(self,
                                      task_name: str,
                                      callback: Callable[[np.ndarray], None],
                                      interval_ms: float = 100.0) -> bool:
        """
        Start continuous background data acquisition.

        Reads data in a background thread and calls the callback with each
        new data set.

        Args:
            task_name: Name of the task to acquire from
            callback: Function to call with each new data array
            interval_ms: Read interval in milliseconds

        Returns:
            True if acquisition started, False otherwise
        """
        if task_name in self._background_threads:
            log.warning("Background acquisition already running for '%s'", task_name)
            return False

        stop_event = threading.Event()
        self._running[task_name] = stop_event
        self._data_callbacks[task_name] = callback

        def _acquisition_loop():
            """Background loop for continuous data acquisition."""
            log.info("Starting background acquisition for '%s'", task_name)

            while not stop_event.is_set():
                try:
                    # Start task if not running
                    task_info = self._tasks.get(task_name)
                    if task_info and task_info.state != TaskState.RUNNING:
                        self.start_task(task_name)

                    # Read data
                    data = self.read_analog(task_name, timeout=interval_ms / 1000.0 + 0.5)

                    if data is not None and callback is not None:
                        try:
                            callback(data)
                        except Exception as cb_e:
                            log.error("Callback error for '%s': %s", task_name, cb_e)

                except Exception as e:
                    log.error("Background acquisition error for '%s': %s", task_name, e)

                # Wait for next interval
                stop_event.wait(interval_ms / 1000.0)

            log.info("Background acquisition stopped for '%s'", task_name)

        thread = threading.Thread(
            target=_acquisition_loop,
            name=f"acq-{task_name}",
            daemon=True
        )
        thread.start()

        self._background_threads[task_name] = thread
        log.info("Started background acquisition for '%s' (interval=%d ms)",
                 task_name, interval_ms)

        return True

    def start_background_output(self,
                                 task_name: str,
                                 data_generator: Callable[[], np.ndarray],
                                 interval_ms: float = 100.0) -> bool:
        """
        Start continuous background analog output.

        Generates and writes data in a background thread.

        Args:
            task_name: Name of the output task
            data_generator: Function that returns data array for each write
            interval_ms: Write interval in milliseconds

        Returns:
            True if output started, False otherwise
        """
        if task_name in self._background_threads:
            log.warning("Background output already running for '%s'", task_name)
            return False

        stop_event = threading.Event()
        self._running[task_name] = stop_event

        def _output_loop():
            """Background loop for continuous output."""
            log.info("Starting background output for '%s'", task_name)

            while not stop_event.is_set():
                try:
                    task_info = self._tasks.get(task_name)
                    if task_info and task_info.state != TaskState.RUNNING:
                        self.start_task(task_name)

                    data = data_generator()
                    if data is not None:
                        self.write_analog(task_name, data, auto_start=False)

                except Exception as e:
                    log.error("Background output error for '%s': %s", task_name, e)

                stop_event.wait(interval_ms / 1000.0)

            log.info("Background output stopped for '%s'", task_name)

        thread = threading.Thread(
            target=_output_loop,
            name=f"out-{task_name}",
            daemon=True
        )
        thread.start()

        self._background_threads[task_name] = thread
        log.info("Started background output for '%s'", task_name)

        return True

    def stop_background_acquisition(self, task_name: str) -> bool:
        """
        Stop background acquisition for a task.

        Args:
            task_name: Name of the task to stop

        Returns:
            True if stopped, False if not running
        """
        if task_name in self._running:
            self._running[task_name].set()
            del self._running[task_name]

        if task_name in self._data_callbacks:
            del self._data_callbacks[task_name]

        if task_name in self._background_threads:
            thread = self._background_threads.pop(task_name)

            # Wait briefly for thread to stop
            if thread.is_alive():
                thread.join(timeout=1.0)

            log.info("Stopped background processing for '%s'", task_name)
            return True

        return False

    def get_task_info(self, task_name: str) -> Optional[TaskInfo]:
        """
        Get information about a task.

        Args:
            task_name: Name of the task

        Returns:
            TaskInfo if task exists, None otherwise
        """
        return self._tasks.get(task_name)

    def get_all_tasks(self) -> List[TaskInfo]:
        """
        Get information about all active tasks.

        Returns:
            List of TaskInfo objects
        """
        return list(self._tasks.values())

    def get_active_tasks(self) -> List[TaskInfo]:
        """
        Get information about all running tasks.

        Returns:
            List of TaskInfo objects for running tasks
        """
        return [
            task for task in self._tasks.values()
            if task.state == TaskState.RUNNING
        ]

    def _update_task_state(self, task_name: str,
                           state: TaskState,
                           error_msg: str = "") -> None:
        """
        Update the state of a task.

        Args:
            task_name: Name of the task
            state: New task state
            error_msg: Error message if state is ERROR
        """
        with self._lock:
            task_info = self._tasks.get(task_name)
            if task_info:
                task_info.state = state
                task_info.error_message = error_msg

    def _cleanup_task(self, task_name: str) -> None:
        """
        Clean up a task that failed to create properly.

        Args:
            task_name: Name of the task to clean up
        """
        with self._lock:
            if task_name in self._nidaqmx_tasks:
                try:
                    self._nidaqmx_tasks[task_name].close()
                except Exception:
                    pass
                del self._nidaqmx_tasks[task_name]

            self._tasks.pop(task_name, None)

    def cleanup_all(self) -> None:
        """
        Clean up all tasks and resources.

        Should be called when the application shuts down.
        """
        log.info("Cleaning up all tasks...")

        # Stop all background threads
        for task_name in list(self._running.keys()):
            self.stop_background_acquisition(task_name)

        # Clear all NI-DAQmx tasks
        with self._lock:
            for task_name in list(self._nidaqmx_tasks.keys()):
                try:
                    task = self._nidaqmx_tasks[task_name]
                    try:
                        task.stop()
                    except Exception:
                        pass
                    task.close()
                except Exception as e:
                    log.warning("Error cleaning up task '%s': %s", task_name, e)

            self._nidaqmx_tasks.clear()
            self._tasks.clear()

        log.info("All tasks cleaned up")