# ATS Test System

A web-based automated test system for controlling National Instruments DAQ hardware using the NI-DAQmx Python API. The browser UI supports USB and Ethernet CompactDAQ devices, relay mapping tables, and digital I/O diagnostics for ATS test fixtures.

## Features

- **Automatic device discovery** — Detects NI DAQ devices (Ethernet CompactDAQ, USB, PXI, PCIe)
- **Multi-device support** — USB and Ethernet devices shown together in one interface
- **Relay mapping tables** — RTD, POT, DI Dry, DI Wet, and AO relay maps driven by `static/daq_usb_config.js`
- **Digital I/O** — Per-line read/write, port selection, and sequential line diagnostics
- **Analog I/O** — Single-sample and continuous acquisition; DC/AC analog output
- **Dashboard** — Device overview, system log, and device-detection troubleshooting steps
- **Network access** — Server binds to `0.0.0.0` for use from other machines on the LAN
- **Logging** — Operations logged to console and `logs/ats_test_system.log`

## Architecture

```
NI_DAQ_Controller/
├── start                   # Short launcher: python start
├── start_web_server.py     # Universal launcher (run from any folder)
├── web_app.py            # Flask web server and REST API
├── device_manager.py     # Device discovery and management
├── analog_input.py       # Analog input operations
├── analog_output.py      # Analog output operations
├── digital_io.py         # Digital I/O operations
├── module_manager.py     # Module detection and configuration
├── task_manager.py       # NI-DAQmx task management
├── logger.py             # Logging system
├── config.py             # Application configuration (~/.ats_test_system/)
├── utils.py              # Utility functions
├── static/
│   └── daq_usb_config.js # Relay ↔ NI line mappings (RTD, POT, DI, AO)
├── templates/
│   └── index.html        # Browser-based user interface
├── logs/                 # Runtime log files
├── requirements.txt      # Python dependencies
└── README.md             # This file
```

## Requirements

- Python 3.11+
- NI-DAQmx Runtime (installed with NI hardware drivers)
- NI-DAQmx Python library (`nidaqmx`)
- Modern web browser (Chrome, Firefox, Edge, Safari)

## Installation

1. Install NI-DAQmx (included with NI hardware driver installation).
2. Install Python dependencies:

```bash
cd NI_DAQ_Controller
pip install -r requirements.txt
```

## Usage

### Start the server

From the app folder:

```bash
cd NI_DAQ_Controller
python start
```

(`start` launches `web_app.py`. You can also run `python web_app.py` directly.)

Or from anywhere in the repository:

```bash
python NI_DAQ_Controller/start_web_server.py
```

### Open the web interface

```
http://localhost:5000
```

On Windows, you can also double-click **`start`** or **`start_web_server.py`** in the `NI_DAQ_Controller` folder.

### Dashboard

The Dashboard shows connected devices and the system log. If no devices are detected, follow the on-screen steps:

1. **Refresh** — Click **Refresh** in the toolbar to rescan for connected devices.
2. **Check wiring** — Verify each NI DAQ device is powered on and USB or Ethernet cables are securely connected.
3. **Restart the server** — If devices are still not detected, stop and restart the ATS Test System server, then click **Refresh** again.

For Ethernet CompactDAQ, you can also add a device by IP from the Dashboard when no hardware is found.

## Web interface

### Device tabs

- Each detected device appears as its own chassis tab (e.g. **USB**, **Ethernet - device name**).
- Each module on the device appears as a sub-tab (slot and product type).

### USB digital-output modules

For USB modules with digital outputs, these views are available:

| Tab | Purpose |
|-----|---------|
| **NI DAQ** | Raw digital lines grouped by port (filtered by **Select Ports**) |
| **DI Dry** | Dry-section relay map (digital outputs) |
| **DI Wet** | Wet-section relay map |
| **AO Map** | Analog-output relay section |
| **RTD** | RTD relay resistance selection |
| **POT** | Potentiometer relay map |
| **Select Ports** | Choose which `P{port}.{line}` lines appear on the NI DAQ tab |
| **Diagnostics** | Sequential test of each digital line (True/False) |

Close the NI MAX Test Panel before writing to lines from the web UI.

### Ethernet digital-input modules

Ethernet DI modules use **Channels** (relay pair read) and **Select** (choose which relay pairs to show).

## Configuring relay ↔ NI line mappings

All relay mapping tables are defined in **`static/daq_usb_config.js`**. Each row includes an `niLine` in the format `P{port}.{line}` (for example `P5.3` = port 5, line 3).

```javascript
{ ch: 10, relay: 'K26', net: 'DI_10', pin: '10', niLine: 'P5.3' },
```

To change which NI line drives a relay, edit only the `niLine` value for that row, save the file, and hard-refresh the browser (Ctrl+F5).

**You do not need to change** `index.html`, `web_app.py`, or any Python files for mapping updates.

### Mapping checklist

- The new `P{port}.{line}` must exist on your NI DO hardware.
- Avoid assigning the same `niLine` to two relays unless that is intentional (both rows will control the same physical line).
- If a row shows **—** instead of write buttons, the line was not found on the module (check hardware and spelling).
- The **Select Ports** tab only affects the raw **NI DAQ** view; mapping tabs resolve lines directly from `daq_usb_config.js`.

### Sections in `daq_usb_config.js`

| Key | Description |
|-----|-------------|
| `RTD` | RTD relay groups (Ω values per channel) |
| `POT` | Potentiometer relay groups |
| `DI_DRY` | Dry digital-input relay section |
| `DI_WET` | Wet digital-input relay section |
| `AO_MAP` | Analog-output relay section |

## How it works

1. On startup, connected NI DAQ devices are discovered automatically.
2. Each device and module gets UI tabs based on detected channel types.
3. Digital writes go through the Flask REST API to NI-DAQmx tasks.
4. Relay labels (relay name, net name, J23 pin) are display-only; only `niLine` controls which NI channel is used.

## Network access

The web server listens on `0.0.0.0:5000` by default. From another machine on the same network, open:

```
http://<host-ip>:5000
```

## Configuration and logs

| Item | Location |
|------|----------|
| User config | `~/.ats_test_system/config.yaml` |
| Application logs | `NI_DAQ_Controller/logs/ats_test_system.log` |
| Port/line UI selection | Browser `localStorage` (per device name) |

## License

Proprietary — for internal use only.
