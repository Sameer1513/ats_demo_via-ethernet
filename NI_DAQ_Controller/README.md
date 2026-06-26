# NI DAQ Controller

A professional desktop application for controlling National Instruments DAQ devices using the NI-DAQmx Python API.

## Features

- **Automatic Device Discovery**: Detects all connected NI DAQ devices (Ethernet CompactDAQ, USB, PXI, PCIe)
- **Dynamic GUI**: Automatically generates tabs and controls based on connected hardware
- **Multi-Device Support**: Simultaneously work with multiple NI devices
- **Analog I/O**: Single sample and continuous acquisition with configurable parameters
- **Analog Output**: DC/AC signal generation with various waveforms
- **Digital I/O**: Digital input, output, relay, counter, and timer support
- **Live Monitoring**: Real-time data visualization with configurable refresh rates
- **Dashboard**: Central view of all connected devices, status, and active tasks
- **Data Export**: Export acquired data to CSV
- **Comprehensive Logging**: All operations, errors, and configuration changes logged
- **Robust Error Handling**: Graceful handling of disconnections, timeouts, and invalid inputs

## Architecture

```
NI_DAQ_Controller/
├── main.py              # Application entry point
├── gui.py               # Main GUI window (CustomTkinter)
├── device_manager.py    # Device discovery and management
├── device_tab.py        # Dynamic device tab creation
├── module_manager.py    # Module detection and configuration
├── analog_input.py      # Analog input operations
├── analog_output.py     # Analog output operations
├── digital_io.py        # Digital I/O operations
├── task_manager.py      # NI-DAQmx task management
├── logger.py            # Logging system
├── config.py            # Configuration management
├── utils.py             # Utility functions
├── requirements.txt     # Python dependencies
└── README.md           # Documentation
```

## Requirements

- Python 3.11+
- NI-DAQmx Runtime (installed with NI hardware drivers)
- NI-DAQmx Python library

## Installation

1. Install NI-DAQmx (included with NI hardware driver installation)
2. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Run the application:
   ```bash
   python main.py
   ```

## Usage

### Web Interface (recommended)

```bash
cd NI_DAQ_Controller
python web_app.py
```
Then open **http://localhost:5000** in your browser.

### Desktop GUI

```bash
cd NI_DAQ_Controller
python main.py
```

### Universal launcher (run from any folder)

```bash
python path/to/NI_DAQ_Controller/start_web_server.py
```
Then open **http://localhost:5000**

### Windows (double-click)

Double-click **`run.bat`** in the `NI_DAQ_Controller` folder.

## How It Works

1. Connected NI DAQ devices are automatically detected on startup
2. Each device appears as a separate tab (web) or window tab (desktop)
3. Within each device, detected modules are shown as collapsible sections
4. Configure read/write operations per module using the generated controls
5. Use the Dashboard tab for an overview of all devices
6. Use the Refresh button (or F5) to rescan for new devices
7. Without NI hardware connected, the app shows "No devices detected" — this is correct

## License

Proprietary - For internal use only