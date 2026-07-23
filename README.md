# ATS Test System

A web-based automated test system for controlling National Instruments DAQ hardware using the NI-DAQmx Python API. The browser UI supports USB and Ethernet CompactDAQ devices, relay mapping tables, and digital I/O diagnostics for ATS test fixtures.

## File Structure

```
ats_demo_via-ethernet/
├── ni_daq_controller/           # Main application package
│   ├── constants.py             # Centralized constants and config defaults
│   ├── start                    # Launcher: run from ni_daq_controller folder
│   ├── start_web_server.py      # Universal launcher: run from anywhere
│   ├── requirements.txt         # Python dependencies
│   ├── web/
│   │   └── app.py               # Flask web server (port 5000) & REST API
│   ├── core/
│   │   ├── device_manager.py    # Device discovery and management
│   │   ├── module_manager.py    # Module detection and configuration
│   │   └── task_manager.py      # NI-DAQmx task management
│   ├── daq_io/
│   │   ├── input.py             # Analog input operations
│   │   ├── output.py            # Analog output operations (DC + AC)
│   │   └── digital_io.py        # Digital I/O operations
│   ├── app_logging/
│   │   └── logger.py            # Logging system
│   ├── utils/
│   │   ├── config.py            # Application configuration
│   │   └── utils.py             # Utility functions
│   └── logs/
│       └── ni_daq_controller.log # Runtime log files
├── ui/                          # Browser interface
│   ├── templates/
│   │   └── index.html           # Main UI page
│   └── static/
│       └── daq_usb_config.js    # Relay to NI line mappings
├── .venv/                       # Python virtual environment (created by user)
│   ├── Scripts/activate         # Activate venv (Windows)
│   └── ...
└── README.md                    # This file
```

## Requirements

- Python 3.11 or higher
- NI-DAQmx Runtime 2021 Q3 or later (from ni.com)
- NI-DAQmx Python library (installed via requirements.txt)
- NI DAQ hardware (USB CompactDAQ or Ethernet cDAQ)
- Modern web browser (Chrome, Firefox, Edge, Safari)

## Installation

1. Download and install NI-DAQmx Runtime from https://www.ni.com/en/support/downloads/drivers/download.ni-daqmx.html
2. Open terminal in `ats_demo_via-ethernet` folder
3. Create virtual environment:
   ```bash
   python -m venv .venv
   ```
4. Activate virtual environment:
   ```bash
   .venv\Scripts\activate
   ```
5. Install dependencies:
   ```bash
   pip install -r nidaq_controller\requirements.txt
   ```

## Running the Application

With the virtual environment activated, start the web server:

```bash
cd ni_daq_controller
python start
```

Or from anywhere:

```bash
py -3 ni_daq_controller\start_web_server.py
```

Open browser to: `http://localhost:5000`

## Usage

Once the server is running, open http://localhost:5000 in your browser. The dashboard will display detected NI DAQ devices. Each device appears as a tab with module sub-tabs for AI, AO, DI, DO operations.

For Ethernet cDAQ devices that are not auto-discovered, use the "Add Network Device" option in the dashboard and enter the chassis IP address.

## Relay Mapping

Edit `ui/static/daq_usb_config.js` to configure relay-to-NI-line mappings. Each row has a `niLine` field in format `P{port}.{line}`. After editing, hard-refresh the browser (Ctrl+F5).

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `pip install fails` | Install Python 3.11+ from python.org with "Add to PATH" checked |
| `ModuleNotFoundError: No module named 'nidaqmx'` | Run `pip install -r requirements.txt` |
| `OSError: no driver found` | Install NI-DAQmx Runtime |
| Server starts but shows "No devices" | Verify device appears in NI MAX; click Refresh; restart server |
| Cannot connect from another PC | Add firewall rule for port 5000: `New-NetFirewallRule -DisplayName "ATS Test System" -Direction Inbound -LocalPort 5000 -Protocol TCP -Action Allow` |
| Port 5000 already in use | Stop other program or edit `web/app.py` to change port |
| AO write fails: "resource is reserved" | Click Refresh to clear stale tasks; cDAQ modules share AO subsystem |
| Ethernet device not detected | Use "Add Network Device" with chassis IP; verify network connectivity |
| Digital write fails | In NI MAX, set port direction to Output for the lines |

## Configuration

- User config: `~/.ats_test_system/config.yaml`
- Application logs: `ni_daq_controller/logs/ni_daq_controller.log`
- Port/line UI selection: Browser localStorage (per device name)

