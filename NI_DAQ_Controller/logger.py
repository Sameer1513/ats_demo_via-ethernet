"""
Logging module for NI DAQ Controller.

Provides a centralized logging system with file rotation, console output,
and structured log formatting. All application modules use this logger for
consistent log management.

Features:
    - Rotating file logs with configurable size limits
    - Console output with color coding
    - Separate loggers for different subsystems
    - Thread-safe logging operations
    - Context-based filtering

Typical usage:
    from logger import get_logger
    log = get_logger(__name__)
    log.info("Device connected: %s", device_name)
    log.error("Failed to read channel: %s", str(e))
"""

import os
import sys
import logging
import threading
from logging.handlers import RotatingFileHandler
from typing import Dict, Optional
from pathlib import Path
from datetime import datetime


# ANSI color codes for console logging
class _LogColors:
    """ANSI color codes for terminal output formatting."""
    RESET = '\033[0m'
    BOLD = '\033[1m'
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    WHITE = '\033[97m'
    DIM = '\033[2m'

    # Level-specific colors
    LEVEL_COLORS = {
        'DEBUG': CYAN,
        'INFO': GREEN,
        'WARNING': YELLOW,
        'ERROR': RED,
        'CRITICAL': RED + BOLD,
    }


class _ColoredFormatter(logging.Formatter):
    """
    Custom formatter that adds ANSI color codes to console output.

    Formats log messages with different colors based on severity level
    for improved readability in the terminal.
    """

    def format(self, record: logging.LogRecord) -> str:
        """
        Format the log record with color coding.

        Args:
            record: The log record to format

        Returns:
            Color-formatted log string
        """
        # Save original values
        original_msg = record.msg
        original_levelname = record.levelname

        # Color the level name
        color = _LogColors.LEVEL_COLORS.get(
            record.levelname, _LogColors.WHITE
        )
        record.levelname = (
            f"{color}{record.levelname:<8}{_LogColors.RESET}"
        )

        # Color the logger name
        record.name = (
            f"{_LogColors.DIM}{record.name}{_LogColors.RESET}"
        )

        # Format the message
        result = super().format(record)

        # Restore original values
        record.msg = original_msg
        record.levelname = original_levelname
        record.name = original_name = record.name.split(_LogColors.RESET)[0]\
            .replace(f"{_LogColors.DIM}", "")\
            .replace(f"{_LogColors.RESET}", "")

        return result


class LogManager:
    """
    Centralized log manager for the application.

    Manages loggers, handlers, and configuration for the entire application.
    Provides a singleton pattern for global log management.

    Attributes:
        _loggers: Dictionary of created loggers
        _lock: Thread lock for safe concurrent access
        log_dir: Directory for log files
        config: Loaded configuration
        _initialized: Whether the manager has been initialized
    """

    _instance: Optional['LogManager'] = None
    _lock = threading.Lock()

    def __new__(cls) -> 'LogManager':
        """
        Create or return the singleton instance.

        Returns:
            LogManager singleton instance
        """
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        """Initialize the log manager (only once)."""
        if self._initialized:
            return

        self._loggers: Dict[str, logging.Logger] = {}
        self._lock = threading.Lock()
        self.log_dir: Optional[Path] = None
        self._initialized = True

        # Set up the root logger
        self._setup_root_logger()

    def initialize(self, log_dir: Optional[str] = None,
                   level: str = 'INFO',
                   max_file_size_mb: int = 10,
                   backup_count: int = 5,
                   log_to_console: bool = True,
                   log_to_file: bool = True) -> None:
        """
        Initialize the log manager with configuration.

        Args:
            log_dir: Directory for log files. Defaults to 'logs/'
            level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
            max_file_size_mb: Maximum log file size in MB before rotation
            backup_count: Number of rotated log files to keep
            log_to_console: Whether to output logs to console
            log_to_file: Whether to output logs to file
        """
        if log_dir:
            self.log_dir = Path(log_dir)
        else:
            self.log_dir = Path.cwd() / 'logs'

        # Create log directory
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            print(f"Warning: Could not create log directory: {e}")
            self.log_dir = Path.cwd()

        # Set numeric level
        numeric_level = getattr(logging, level.upper(), logging.INFO)

        # Configure root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(numeric_level)

        # Remove existing handlers
        root_logger.handlers.clear()

        # Add console handler
        if log_to_console:
            self._add_console_handler(root_logger, numeric_level)

        # Add file handler
        if log_to_file:
            self._add_file_handler(
                root_logger, numeric_level,
                max_file_size_mb, backup_count
            )

        # Log initialization
        logging.getLogger(__name__).info(
            "Logging initialized - Level: %s, Directory: %s",
            level, self.log_dir
        )

    def _setup_root_logger(self) -> None:
        """
        Set up the root logger with basic configuration.

        Ensures there is always at least a basic console handler available
        before full initialization.
        """
        root_logger = logging.getLogger()
        if not root_logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            formatter = logging.Formatter(
                '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
                datefmt='%H:%M:%S'
            )
            handler.setFormatter(formatter)
            root_logger.addHandler(handler)
            root_logger.setLevel(logging.INFO)

    def _add_console_handler(self, logger: logging.Logger,
                             level: int) -> None:
        """
        Add a colored console handler to the logger.

        Args:
            logger: Logger to add handler to
            level: Logging level for the handler
        """
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)

        console_formatter = _ColoredFormatter(
            '%(asctime)s | %(levelname)s | %(name)s | %(message)s',
            datefmt='%H:%M:%S'
        )
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

    def _add_file_handler(self, logger: logging.Logger,
                          level: int,
                          max_file_size_mb: int,
                          backup_count: int) -> None:
        """
        Add a rotating file handler to the logger.

        Args:
            logger: Logger to add handler to
            level: Logging level for the handler
            max_file_size_mb: Maximum file size in MB before rotation
            backup_count: Number of backup files to keep
        """
        log_file = self.log_dir / 'ni_daq_controller.log'

        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=max_file_size_mb * 1024 * 1024,
            backupCount=backup_count,
            encoding='utf-8'
        )
        file_handler.setLevel(level)

        file_formatter = logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

    def get_logger(self, name: str) -> logging.Logger:
        """
        Get or create a logger with the given name.

        Args:
            name: Logger name (typically __name__)

        Returns:
            Configured logger instance
        """
        # Return existing logger if already created
        if name in self._loggers:
            return self._loggers[name]

        # Create new logger
        logger = logging.getLogger(name)

        with self._lock:
            self._loggers[name] = logger

        return logger

    def set_level(self, level: str) -> None:
        """
        Set the logging level for all handlers.

        Args:
            level: New logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        """
        numeric_level = getattr(logging, level.upper(), logging.INFO)
        root_logger = logging.getLogger()

        root_logger.setLevel(numeric_level)
        for handler in root_logger.handlers:
            handler.setLevel(numeric_level)

        logging.getLogger(__name__).info(
            "Log level changed to: %s", level
        )

    def get_log_files(self) -> list:
        """
        Get list of log files in the log directory.

        Returns:
            List of Path objects for log files
        """
        if self.log_dir and self.log_dir.exists():
            return sorted(self.log_dir.glob('*.log*'), reverse=True)
        return []


# Global log manager instance
log_manager = LogManager()


def get_logger(name: str) -> logging.Logger:
    """
    Convenience function to get a logger.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Configured logger instance
    """
    return log_manager.get_logger(name)


def initialize_logging(level: str = 'INFO',
                       log_dir: Optional[str] = None) -> None:
    """
    Initialize the logging system.

    Args:
        level: Logging level
        log_dir: Directory for log files
    """
    log_manager.initialize(level=level, log_dir=log_dir)