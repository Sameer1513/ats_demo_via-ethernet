# NI DAQ Controller

A professional desktop application for controlling National Instruments DAQ devices using the NI-DAQmx Python API.

## Features

- **Automatic Device Discovery**: Detects all connected NI DAQ devices (Ethernet CompactDAQ, USB, PXI, PCIe)
- **Dynamic Web Interface**: Automatically generates browser-based controls based on connected hardware
- **Multi-Device Support**: Simultaneously work with multiple NI devices
- **Analog I/O**: Single sample and continuous acquisition with configurable parameters
- **Analog Output**: DC/AC signal generation with various waveforms
- **Digital I/O**: Digital input, output, relay, counter, and timer support
- **Live Monitoring**: Real-time data visualization with configurable refresh rates
- **Dashboard**: Central view of all connected devices, status, and active tasks
- **Data Export**: Export acquired data to CSV
- **Comprehensive Logging**: All operations, errors, and configuration changes logged
- **Robust Error Handling**: Graceful handling of disconnections, timeouts, and invalid inputs
- **Access Anywhere**: Use from any device on the network via browser

## Architecture

```
NI_DAQ_Controller/
├── start_web_server.py  # Universal launcher (run from any folder)
├── web_app.py           # Flask web server and REST API
├── device_manager.py    # Device discovery and management
├── analog_input.py      # Analog input operations
├── analog_output.py     # Analog output operations
├── digital_io.py        # Digital I/O operations
├── module_manager.py    # Module detection and configuration
├── task_manager.py      # NI-DAQmx task management
├── logger.py            # Logging system
├── config.py            # Configuration management
├── utils.py             # Utility functions
├── templates/
│   └── index.html       # Browser-based user interface
├── requirements.txt     # Python dependencies
└── README.md           # Documentation
```

## Requirements

- Python 3.11+
- NI-DAQmx Runtime (installed with NI hardware drivers)
- NI-DAQmx Python library
- Modern web browser (Chrome, Firefox, Edge, Safari)

## Installation

1. Install NI-DAQmx (included with NI hardware driver installation)
2. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Usage

### Start the server

From the app folder:
```bash
cd NI_DAQ_Controller
python web_app.py
```

Or from anywhere:
```bash
python NI_DAQ_Controller/start_web_server.py
```

### Open the web interface

Open your browser and go to:
```
http://localhost:5000
```

### Windows (double-click)

If installed on Windows, double-click **`start_web_server.py`** in the `NI_DAQ_Controller` folder.

## How It Works

1. Connected NI DAQ devices are automatically detected on startup
2. Each device appears as a separate browser tab
3. Within each device, detected modules are shown as collapsible sections
4. Configure read/write operations per module using the generated controls
5. Use the Dashboard tab for an overview of all devices
6. Use the Refresh button to rescan for new devices
7. Without NI hardware connected, the app shows "No devices detected" — this is correct

## Network Access

The web server binds to `0.0.0.0` by default, so you can access it from other devices on the same network using your computer's IP address. This is especially useful for test stations where the DAQ is headless.

## License

Proprietary - For internal use only