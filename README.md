# ATS Test System

A web-based automated test system for controlling National Instruments DAQ hardware using the NI-DAQmx Python API. The browser UI supports USB and Ethernet CompactDAQ devices, relay mapping tables, and digital I/O diagnostics for ATS test fixtures.

## Features

- Automatic device discovery — Detects NI DAQ devices (Ethernet CompactDAQ, USB, PXI, PCIe)
- Multi-device support — USB and Ethernet devices shown together in one interface
- Relay mapping tables — RTD, POT, DI Dry, DI Wet, and AO relay maps driven by static/daq_usb_config.js
- Digital I/O — Per-line read/write, port selection, and sequential line diagnostics
- Analog I/O — Single-sample and continuous acquisition; DC/AC analog output
- Dashboard — Device overview, system log, and device-detection troubleshooting steps
- Network access — Server binds to 0.0.0.0 for use from other machines on the LAN
- Logging — Operations logged to console and logs/ats_test_system.log

## Architecture

Project Root/
  ni_daq_controller/
    __init__.py             Package marker
    start                   Short launcher: python start
    start_web_server.py     Universal launcher (run from any folder)
    requirements.txt        Python dependencies
    TODO.md                 Task checklist
    logs/
      ni_daq_controller.log Runtime log files
    app_logging/
      __init__.py           Package marker
      logger.py             Logging system
    core/
      __init__.py           Package marker
      device_manager.py     Device discovery and management
      module_manager.py     Module detection and configuration
      task_manager.py       NI-DAQmx task management
    daq_io/
      __init__.py           Package marker
      input.py              Analog input operations
      output.py             Analog output operations
      digital_io.py         Digital I/O operations
    utils/
      __init__.py           Package marker
      config.py             Application configuration
      utils.py              Utility functions
    web/
      app.py                Flask web server and REST API
  ui/
    templates/
      index.html            Browser-based user interface
    static/
      daq_usb_config.js     Relay to NI line mappings
  requirements.txt          (optional, duplicate)

## Requirements

- Python 3.11+ (python.org or Microsoft Store)
- NI-DAQmx Runtime 2021 Q3 or later — Required even if no NI hardware is connected during initial setup.
- NI-DAQmx Python library (nidaqmx)
- Modern web browser (Chrome, Firefox, Edge, Safari)
- Windows 10/11 (recommended) — Linux/macOS are not supported for NI-DAQmx.

## Installation — Setup Steps

> **Quick setup (USB devices):** Install NI-DAQmx Runtime → `pip install -r requirements.txt` → `python start` → open `http://localhost:5000`

### 🔧 Step 1: Install NI-DAQmx Runtime

Download and install NI-DAQmx Runtime from one of these sources:

  • **NI Package Manager** — Search for "NI-DAQmx" and install the latest version
  • **Direct download** — https://www.ni.com/en/support/downloads/drivers/download.ni-daqmx.html (free, requires NI user account)

> ⚠️ **Without NI-DAQmx Runtime installed, the server will start but show "No NI DAQ Devices Detected".** The Python package `nidaqmx` (installed via pip) is only the API wrapper — it does not replace the NI-DAQmx Runtime.

### 📦 Step 2: Install Python Dependencies

Open a terminal (Command Prompt or PowerShell) and run:

```bash
cd ni_daq_controller
pip install -r requirements.txt
```

If you have multiple Python versions, use:

```bash
py -3.11 -m pip install -r requirements.txt
```

### 🌐 Step 3: Configure Ethernet cDAQ Chassis IP (Ethernet devices only)

If you are using an Ethernet CompactDAQ (cDAQ) chassis, set a static IP on your laptop's Ethernet adapter and configure the chassis IP in NI MAX.

**3a. Set a static IP on your laptop:**

1. Open Control Panel → Network and Sharing Center → Change adapter settings.
2. Right-click your Ethernet adapter → Properties.
3. Select Internet Protocol Version 4 (TCP/IPv4) → Properties.
4. Choose "Use the following IP address" and enter:

   ```
   IP address:    169.254.1.1
   Subnet mask:   255.255.0.0
   Default gateway: (leave blank)
   ```

5. Click OK to save.

> 💡 The cDAQ chassis typically uses a link-local address in the 169.254.x.x range. The last octet (.1.1 above) can be any number not used by another device on the same link.

**3b. Configure the cDAQ chassis IP in NI MAX:**

1. Connect the cDAQ chassis to your laptop via an Ethernet cable.
2. Open NI MAX (search for it in the Start menu).
3. Under Remote Systems, you should see the cDAQ chassis appear (this may take a few seconds).
4. If the chassis does not appear:
   - Click **Create New → Remote System**.
   - Enter the chassis hostname (printed on the chassis label) or its default IP (e.g. 169.254.x.x).
5. Right-click the chassis → Properties → Network Settings.
6. Note or set the chassis IP address (e.g. 169.254.1.2). Ensure it is on the same subnet as your laptop (169.254.x.x).
7. Click Apply and wait for the chassis to restart if prompted.
8. The chassis should now show as **Connected** in NI MAX.

> ✅ Once the chassis is reserved in NI MAX, the ATS Test System will detect it automatically on startup or after clicking Refresh.

### ✅ Step 4: Verify NI-DAQmx Is Recognised (optional)

```bash
python -c "import nidaqmx; print('OK:', nidaqmx.system.System.local().device_names)"
```

If this prints a device list (which may be empty if no hardware is attached) without errors, NI-DAQmx is installed correctly.

## Usage — Execution Steps

### 🚀 Step 1: Start the Server

From the app folder:

```bash
cd ni_daq_controller
python start
```

> `start` launches `web/app.py`. You can also run `python web/app.py` directly.

Or from anywhere in the repository:

```bash
py -3 ni_daq_controller\start_web_server.py
```

### 🌐 Step 2: Open the Web Interface

Open your browser and go to:

```
http://localhost:5000
```

On Windows, you can also double-click `start` or `start_web_server.py` in the `ni_daq_controller` folder.

### 📊 Step 3: Use the Dashboard

The Dashboard shows connected devices and the system log. If no devices are detected, follow the on-screen steps:

1. **Refresh** — Click Refresh in the toolbar to rescan for connected devices.
2. **Check wiring** — Verify each NI DAQ device is powered on and USB or Ethernet cables are securely connected.
3. **Check NI MAX** — Open NI MAX. If the device appears there but not in the web UI, click Refresh again. If it doesn't appear in NI MAX either, the NI-DAQmx driver or hardware connection may need attention.
4. **Restart the server** — Stop and restart the ATS Test System server, then click Refresh again.

For Ethernet CompactDAQ, you can also add a device by IP address from the Dashboard when no hardware is found.

## Web Interface

Device tabs

- Each detected device appears as its own chassis tab (e.g. USB, "Ethernet - device name").
- Each module on the device appears as a sub-tab (slot and product type).

USB digital-output modules

For USB modules with digital outputs, these views are available:

  NI DAQ         — Raw digital lines grouped by port (filtered by Select Ports)
  DI Dry         — Dry-section relay map (digital outputs)
  DI Wet         — Wet-section relay map
  AO Map         — Analog-output relay section
  RTD            — RTD relay resistance selection
  POT            — Potentiometer relay map
  Select Ports   — Choose which P{port}.{line} lines appear on the NI DAQ tab
  Diagnostics    — Sequential test of each digital line (True/False)

Close the NI MAX Test Panel before writing to lines from the web UI.

Ethernet digital-input modules

Ethernet DI modules use Channels (relay pair read) and Select (choose which relay pairs to show).

## Analog Input Terminal Configuration

Analog input channels can be read using one of three terminal configurations. The
terminal configuration determines how the DAQ device measures the voltage on each
AI channel and should match your sensor/signal wiring for accurate readings.

| Config        | NI-DAQmx name | Use case |
|---------------|---------------|----------|
| RSE           | `RSE`         | Each channel measured relative to the common ground (COM). Use when all signal sources share a ground with the DAQ. |
| NRSE          | `NRSE`        | Each channel measured relative to an isolated module ground (not chassis COM). Reduces ground-loop noise when sources are not tied to the DAQ ground. |
| Differential  | `DIFF`        | Each channel measured as the difference between a (+) and (−) input pair. Best noise rejection for floating or isolated signals. |

### Selecting a terminal config in the web UI

Each module with analog inputs shows a **Terminal** dropdown in the Analog Input
toolbar. Choose **RSE**, **NRSE**, or **Differential** and then click **Read All**
(or the per-channel **Read** button). The selected config is stored per module
and applied to every read from that module. The toast message shows which config
was used (e.g. `Read 4/4 analog input channel(s) (NRSE)`).

### Selecting a terminal config in Python

```python
from daq_io.input import AnalogInputController, TerminalConfig

ai = AnalogInputController(task_manager, module_info)

# Using the enum
ai.read_single_sample(["ai0"], terminal_config=TerminalConfig.NRSE)

# Using a string (case-insensitive; "DIFF" and "Differential" both work)
ai.read_single_sample(["ai0"], terminal_config="DIFF")

# Convenience methods (one-shot reads with a fixed config)
ai.read_single_sample_rse(["ai0"])
ai.read_single_sample_nrse(["ai0"])
ai.read_single_sample_differential(["ai0"])

# Continuous acquisition
ai.start_continuous_acquisition(
    ["ai0", "ai1"], sample_rate=1000.0, num_samples=100,
    terminal_config=TerminalConfig.DIFFERENTIAL,
    data_callback=my_callback,
)
```

### REST API

Both `/api/ai/read` and `/api/ai/start` accept a `terminal_config` field in the
JSON body (defaults to `"RSE"`):

```bash
curl -X POST http://localhost:5000/api/ai/read \
  -H "Content-Type: application/json" \
  -d '{"device_idx":0,"module_idx":0,"channels":["ai0"],"terminal_config":"NRSE"}'
```

Accepted values: `RSE`, `NRSE`, `DIFF`, `Differential` (case-insensitive).

## Configuring Relay to NI Line Mappings

All relay mapping tables are defined in static/daq_usb_config.js. Each row includes an niLine in the format P{port}.{line} (for example P5.3 = port 5, line 3).

  { ch: 10, relay: 'K26', net: 'DI_10', pin: '10', niLine: 'P5.3' }

To change which NI line drives a relay, edit only the niLine value for that row, save the file, and hard-refresh the browser (Ctrl+F5).

You do not need to change index.html, web_app.py, or any Python files for mapping updates.

Mapping checklist

- The new P{port}.{line} must exist on your NI DO hardware.
- Avoid assigning the same niLine to two relays unless that is intentional (both rows will control the same physical line).
- If a row shows — instead of write buttons, the line was not found on the module (check hardware and spelling).
- The Select Ports tab only affects the raw NI DAQ view; mapping tabs resolve lines directly from daq_usb_config.js.

Sections in daq_usb_config.js

  RTD       RTD relay groups (Ω values per channel)
  POT       Potentiometer relay groups
  DI_DRY    Dry digital-input relay section
  DI_WET    Wet digital-input relay section
  AO_MAP    Analog-output relay section

## How It Works

1. On startup, connected NI DAQ devices are discovered automatically.
2. Each device and module gets UI tabs based on detected channel types.
3. Digital writes go through the Flask REST API to NI-DAQmx tasks.
4. Relay labels (relay name, net name, J23 pin) are display-only; only niLine controls which NI channel is used.

## Network Access

The web server listens on 0.0.0.0:5000 by default. From another machine on the same network, open:

  http://<host-ip>:5000

To find the host IP address, run "ipconfig" in a terminal and look for the IPv4 Address of your active network adapter.

Windows Firewall

If other machines cannot connect, Windows Firewall may block port 5000. Allow it with one of these methods:

  Option A — PowerShell (Run as Administrator):
  New-NetFirewallRule -DisplayName "ATS Test System" -Direction Inbound -LocalPort 5000 -Protocol TCP -Action Allow

  Option B — Manual:
  1. Open Windows Defender Firewall with Advanced Security.
  2. Click Inbound Rules -> New Rule...
  3. Choose "Port", then "TCP" -> "Specific local ports: 5000" -> "Allow the connection".
  4. Complete the wizard.

## Troubleshooting

  pip install fails                    Missing Python or pip
                                        Install Python 3.11+ from python.org and ensure "Add to PATH" is checked
  ModuleNotFoundError: No module named 'nidaqmx'
                                        Python packages not installed — Run "pip install -r requirements.txt"
  OSError: no driver found or NidaqError
                                        NI-DAQmx Runtime not installed — Install NI-DAQmx Runtime (see Step 1)
  Server starts but shows "No devices"
                                        No hardware connected or driver issue — Check NI MAX; if device is there, click Refresh. If not there, check USB/Ethernet cables or install NI-DAQmx Runtime
  Cannot connect from another PC        Windows Firewall — Add a firewall rule for port 5000 (see Network access)
  Port 5000 already in use              Another program using the port — Stop the other program, or edit web_app.py (change port=5000 to a different number)
  AO write fails: "resource is reserved" Writing to one AO channel then another on the same cDAQ module — cDAQ modules share the analog output subsystem. The system now stops all existing AO tasks on the module before starting a new one. If the error persists, click Refresh to clear stale tasks.

## Configuration and Logs

  User config             ~/.ats_test_system/config.yaml
  Application logs        ni_daq_controller/logs/ats_test_system.log
  Port/line UI selection  Browser localStorage (per device name)

## License

Proprietary — for internal use only.