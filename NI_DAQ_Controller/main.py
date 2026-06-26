"""
NI DAQ Controller - Main Entry Point

A professional desktop application for controlling National Instruments
DAQ devices using the NI-DAQmx Python API.

This module initializes the logging system, loads configuration, and
launches the main GUI application.

Usage:
    python main.py

Features:
    - Automatic NI DAQ device discovery
    - Dynamic UI generation based on connected hardware
    - Analog input/output with live monitoring
    - Digital I/O with toggle control
    - Waveform generation (sine, square, triangle, sawtooth)
    - CSV data export
    - Continuous acquisition in background threads
    - Comprehensive logging and error handling
    - Dashboard with device overview and system log

Requirements:
    - Python 3.11+
    - NI-DAQmx runtime (from NI driver installation)
    - nidaqmx Python package (pip install nidaqmx)
    - customtkinter (pip install customtkinter)
"""

import sys
import os
import tkinter as tk
from pathlib import Path

# Ensure the package directory is in the path
PACKAGE_DIR = Path(__file__).parent.absolute()
if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))

from logger import initialize_logging, get_logger, log_manager
from config import global_config
from gui import DAQApp


def setup_application() -> bool:
    """
    Set up the application environment before launching GUI.

    Performs:
        - Logging system initialization
        - Configuration loading
        - Log directory creation
        - System environment checks

    Returns:
        True if setup succeeded, False otherwise
    """
    # Initialize logging
    log_dir = Path.cwd() / 'logs'
    log_level = global_config.get('logging.level', 'INFO')

    initialize_logging(
        level=log_level,
        log_dir=str(log_dir),
        max_file_size_mb=global_config.get('logging.max_file_size_mb', 10),
        backup_count=global_config.get('logging.backup_count', 5),
        log_to_console=global_config.get('logging.log_to_console', True),
        log_to_file=global_config.get('logging.log_to_file', True)
    )

    log = get_logger(__name__)
    log.info("=" * 60)
    log.info("NI DAQ Controller v1.0.0")
    log.info("=" * 60)
    log.info("Python version: %s", sys.version)
    log.info("Platform: %s", sys.platform)
    log.info("Working directory: %s", Path.cwd())

    # Check for required packages
    missing_packages = []
    packages_to_check = {
        'nidaqmx': 'NI-DAQmx Python API',
        'numpy': 'NumPy for data handling',
        'customtkinter': 'CustomTkinter GUI framework',
    }

    for package_name, description in packages_to_check.items():
        try:
            __import__(package_name)
            log.debug("Package '%s' (%s) - available", package_name, description)
        except ImportError:
            missing_packages.append(f"  - {package_name}: {description}")
            log.warning("Package '%s' (%s) - NOT installed", package_name, description)

    if missing_packages:
        log.warning("Missing optional packages:")
        for pkg in missing_packages:
            log.warning("%s", pkg)

    # Load application configuration
    try:
        global_config.load()
        log.info("Configuration loaded from: %s", global_config.config_file)
    except Exception as e:
        log.warning("Failed to load configuration: %s. Using defaults.", e)

    # Check for NI-DAQmx runtime
    try:
        import nidaqmx
        from nidaqmx.constants import TerminalConfiguration, AcquisitionType
        log.info("NI-DAQmx library version: %s", getattr(nidaqmx, '__version__', 'unknown'))
    except ImportError:
        log.warning(
            "NI-DAQmx Python library not found. "
            "Install with: pip install nidaqmx"
        )
        log.warning(
            "The application will start but no devices will be detected "
            "until nidaqmx is installed."
        )

    return True


def main() -> None:
    """
    Main application entry point.

    Initializes the application, creates the root window, and starts
    the GUI event loop.
    """
    # Set up application environment
    if not setup_application():
        print("ERROR: Application setup failed. See logs for details.")
        sys.exit(1)

    log = get_logger(__name__)

    try:
        # Create root window
        root = tk.Tk()

        # Create application
        app = DAQApp(root)

        # Log application start
        log.info("Application GUI initialized")
        log.info("Ready for device discovery")
        log.info("Press F5 to refresh devices")

        # Run the application
        app.run()

    except Exception as e:
        log.critical("Fatal application error: %s", e, exc_info=True)
        try:
            import traceback
            error_details = traceback.format_exc()
            log.critical("Traceback:\n%s", error_details)
        except Exception:
            pass

        # Show error dialog
        try:
            import tkinter.messagebox as mb
            mb.showerror(
                "Fatal Error",
                f"A critical error occurred:\n\n{e}\n\n"
                "Please check the log files for details."
            )
        except Exception:
            pass

        sys.exit(1)

    finally:
        log.info("Application terminated")


if __name__ == '__main__':
    main()