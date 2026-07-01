"""
NI DAQ Controller - Web Application Server

A Flask-based web interface for the NI DAQ Controller that exposes
all functionality through a REST API and serves a browser-based UI.

Run this to access the controller via localhost in your browser.

Usage:
    python web_app.py

Then open http://localhost:5000 in your browser.

Supports:
    - Device discovery and management
    - Analog input (single + continuous)
    - Analog output (DC + AC with waveforms)
    - Digital I/O (read + toggle)
    - System log viewing
"""

import sys
import os
import json
import time
import threading
from datetime import datetime
from pathlib import Path

# Ensure package directory is in path
PACKAGE_DIR = Path(__file__).parent.absolute()
if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))

from flask import Flask, jsonify, request, render_template, send_from_directory, make_response

from logger import initialize_logging, get_logger
from config import global_config
from task_manager import TaskManager
from device_manager import DeviceManager, ModuleInfo
from analog_input import AnalogInputController
from analog_output import (
    AnalogOutputController, SignalType, WaveformType, OutputMode,
    convert_to_output_level,
)
from digital_io import DigitalIOController

# Initialize
initialize_logging(level='DEBUG')
log = get_logger(__name__)
# Config is loaded lazily on first access; avoid blocking the network stack here.

# Create global instances
task_manager = TaskManager()
device_manager = DeviceManager()

# Active task tracking
_active_ai_tasks: dict = {}
_active_ao_tasks: dict = {}


def _ao_task_key(device_idx: int, module_idx: int, channel: str) -> str:
    safe = channel.replace('/', '_').replace(':', '_')
    return f"ao_{device_idx}_{module_idx}_{safe}"
_log_entries: list = []
_max_log_entries = 200

app = Flask(__name__, template_folder=str(PACKAGE_DIR / 'templates'),
            static_folder=str(PACKAGE_DIR / 'static'))


def add_log(message: str, level: str = 'INFO') -> None:
    """Add a log entry to the in-memory log buffer."""
    entry = {
        'time': datetime.now().strftime('%H:%M:%S'),
        'message': message,
        'level': level,
        'timestamp': time.time()
    }
    _log_entries.append(entry)
    if len(_log_entries) > _max_log_entries:
        _log_entries.pop(0)
    
    # Also log to the file logger
    getattr(log, level.lower(), log.info)(message)


def get_controller(device_idx: int, module_idx: int):
    """Get a device and module by index from the discovered list."""
    devices = device_manager.get_all_devices()
    if not devices or device_idx >= len(devices):
        return None, None, "Device not found"
    
    device = devices[device_idx]
    if module_idx >= len(device.modules):
        return None, None, "Module not found"
    
    return device, device.modules[module_idx], None


def _build_device_details(devices) -> list:
    """Serialize DeviceInfo list for the JSON API."""
    device_details = []
    for device in devices:
        modules_data = []
        for mod in device.modules:
            modules_data.append({
                'name': mod.name,
                'slot_number': mod.slot_number,
                'product_type': mod.product_type,
                'serial_number': mod.serial_number,
                'supported_operations': mod.supported_operations,
                'ai_channels': mod.ai_channels,
                'ao_channels': mod.ao_channels,
                'di_channels': mod.di_channels,
                'do_channels': mod.do_channels,
                'ci_channels': mod.ci_channels,
                'co_channels': mod.co_channels,
                'voltage_ranges': [f"{r[0]:.1f} to {r[1]:.1f} V" for r in mod.voltage_ranges],
                'max_sample_rate': mod.max_sample_rate,
                'is_simulated': mod.is_simulated,
                'ao_output_unit': (
                    'current' if '9266' in (mod.product_type or '').upper() else 'voltage'
                ),
            })

        device_details.append({
            'name': device.name,
            'product_type': device.product_type,
            'serial_number': device.serial_number,
            'connection_type': device.connection_type.value,
            'ip_address': device.ip_address,
            'status': device.status.value,
            'module_count': len(device.modules),
            'ai_channels': len(device.ai_channels),
            'ao_channels': len(device.ao_channels),
            'di_channels': len(device.di_channels),
            'do_channels': len(device.do_channels),
            'is_simulated': device.is_simulated,
            'modules': modules_data,
        })
    return device_details


def _device_api_payload(devices, *, scanned: bool) -> dict:
    """Build /api/devices JSON body from a device list."""
    device_details = _build_device_details(devices)
    connected = len(device_manager.get_connected_devices())
    total = len(devices)

    if scanned:
        add_log(f"Device discovery: {total} device(s) found")

    hints = []
    if scanned:
        discovery_error = device_manager.get_last_discovery_error()
        if discovery_error:
            hints.append(f"NI-DAQmx scan error: {discovery_error}")
    if not device_details and scanned:
        hints.append(
            "NI MAX may list chassis under Network Devices before they are added to "
            "this PC. In MAX, select the device and click Add Device, then reserve it."
        )

    status = f"{connected}/{total} devices connected"
    if total == 0 and not scanned:
        status = "No devices cached — click Refresh to scan"

    return {
        'devices': device_details,
        'status': status,
        'cached': not scanned,
        'log': _log_entries[-30:],
        'hints': hints,
    }


# ===================== API Routes =====================

@app.route('/')
def index():
    """Serve the main web interface."""
    resp = make_response(render_template('index.html'))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    return resp


@app.route('/api/devices')
def api_devices():
    """API: Return cached devices. Pass ?refresh=1 to rescan hardware."""
    try:
        refresh = request.args.get('refresh', '').lower() in ('1', 'true', 'yes')
        if refresh:
            devices = device_manager.discover_devices()
        else:
            devices = device_manager.get_all_devices()

        return jsonify(_device_api_payload(devices, scanned=refresh))
    except Exception as e:
        log.error("Device API error: %s", e)
        return jsonify({'error': str(e), 'devices': [], 'log': _log_entries[-10:]})


@app.route('/api/devices/add-network', methods=['POST'])
def api_add_network_device():
    """API: Add and reserve an Ethernet cDAQ chassis by IP or hostname."""
    try:
        data = request.json or {}
        ip_or_hostname = (
            data.get('ip_or_hostname')
            or data.get('ip')
            or data.get('hostname')
        )
        if not ip_or_hostname:
            return jsonify({'error': 'ip_or_hostname is required'}), 400

        device_name = (data.get('device_name') or '').strip()
        attempt_reservation = data.get('attempt_reservation', True)
        timeout = float(data.get('timeout', 10.0))

        added_name = device_manager.add_network_device(
            ip_or_hostname.strip(),
            device_name=device_name,
            attempt_reservation=attempt_reservation,
            timeout=timeout,
        )
        devices = device_manager.discover_devices()
        add_log(f"Added network device: {added_name} ({ip_or_hostname})")

        return jsonify({
            'success': True,
            'device_name': added_name,
            'device_count': len(devices),
            **_device_api_payload(devices, scanned=True),
        })
    except Exception as e:
        log.error("Add network device error: %s", e)
        add_log(f"Failed to add network device: {e}", 'ERROR')
        return jsonify({'error': str(e)}), 400


@app.route('/api/ai/read', methods=['POST'])
def api_ai_read():
    """API: Read analog input (single shot)."""
    try:
        data = request.json
        di, mi = data['device_idx'], data['module_idx']
        channels = data.get('channels', [])
        rate = data.get('rate', 1000.0)
        samples = data.get('samples', 10)
        
        device, module, error = get_controller(di, mi)
        if error:
            return jsonify({'error': error}), 400
        
        controller = AnalogInputController(task_manager, module)
        task_name = controller.start_single_acquisition(channels, rate, samples)
        if not task_name:
            return jsonify({'error': 'Failed to start acquisition'}), 500
        
        task_manager.start_task(task_name)
        result = controller.read_data(task_name)
        task_manager.clear_task(task_name)
        
        if result and result.success:
            values = []
            for ch, ch_data in result.data.items():
                if len(ch_data) > 0:
                    values.append(f"{ch}: {ch_data[-1]:.4f} V")
            add_log(f"AI read: {device.name}/{module.name} channels={channels}")
            return jsonify({'values': values, 'success': True})
        
        return jsonify({'error': 'No data returned'}), 500
    except Exception as e:
        log.error("AI read error: %s", e)
        return jsonify({'error': str(e)}), 500


@app.route('/api/ai/start', methods=['POST'])
def api_ai_start():
    """API: Start continuous analog input acquisition."""
    try:
        data = request.json
        di, mi = data['device_idx'], data['module_idx']
        channels = data.get('channels', [])
        rate = data.get('rate', 1000.0)
        samples = data.get('samples', 100)
        
        device, module, error = get_controller(di, mi)
        if error:
            return jsonify({'error': error}), 400
        
        controller = AnalogInputController(task_manager, module)
        
        # Stop any existing continuous task for this module
        task_key = f"{di}_{mi}"
        if task_key in _active_ai_tasks:
            old_ctrl = _active_ai_tasks[task_key]['controller']
            old_ctrl.stop_acquisition(_active_ai_tasks[task_key]['task_name'])
        
        def data_callback(result):
            """Callback for continuous acquisition updates."""
            if result.success:
                values = []
                for ch, ch_data in result.data.items():
                    if len(ch_data) > 0:
                        values.append(f"{ch}: {ch_data[-1]:.4f} V")
                # Store latest values for polling
                _active_ai_tasks[task_key]['latest'] = values
        
        task_name = controller.start_continuous_acquisition(
            channels, rate, samples, data_callback=data_callback
        )
        
        if task_name:
            _active_ai_tasks[task_key] = {
                'task_name': task_name,
                'controller': controller,
                'latest': ['Starting...'],
                'started': time.time()
            }
            add_log(f"Continuous AI started: {device.name}/{module.name}")
            return jsonify({'task_name': task_name, 'success': True})
        
        return jsonify({'error': 'Failed to start'}), 500
    except Exception as e:
        log.error("AI start error: %s", e)
        return jsonify({'error': str(e)}), 500


@app.route('/api/ai/stop', methods=['POST'])
def api_ai_stop():
    """API: Stop continuous analog input acquisition."""
    try:
        data = request.json
        di, mi = data['device_idx'], data['module_idx']
        task_key = f"{di}_{mi}"
        
        if task_key in _active_ai_tasks:
            info = _active_ai_tasks.pop(task_key)
            info['controller'].stop_acquisition(info['task_name'])
            add_log(f"Continuous AI stopped")
            return jsonify({'success': True})
        
        return jsonify({'success': True, 'message': 'No active task'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/ao/start', methods=['POST'])
def api_ao_start():
    """API: Start analog output."""
    try:
        data = request.json
        di, mi = data['device_idx'], data['module_idx']
        channel = data['channel']
        signal = data.get('signal', 'DC')
        value_mode = data.get('value_mode', 'direct')
        value = data.get('value', 1.0)
        frequency = data.get('frequency', 60.0)
        waveform = data.get('waveform', 'Sine')
        amplitude = data.get('amplitude', 1.0)

        device, module, error = get_controller(di, mi)
        if error:
            return jsonify({'error': error}), 400

        controller = AnalogOutputController(task_manager, module)
        is_current = '9266' in (module.product_type or '').upper()
        output_mode = OutputMode.CURRENT if is_current else OutputMode.VOLTAGE
        wf_type = WaveformType(waveform)

        if signal == 'DC':
            value = convert_to_output_level(
                float(value), SignalType.DC, value_mode, wf_type
            )
            applied_display = float(value)
        else:
            amplitude = convert_to_output_level(
                float(amplitude), SignalType.AC, value_mode, wf_type
            )
            applied_display = float(amplitude)

        if is_current:
            value = value / 1000.0
            amplitude = amplitude / 1000.0
            applied_display *= 1000.0

        display_unit = 'mA' if is_current else 'V'
        applied_out = round(applied_display, 4)

        task_key = _ao_task_key(di, mi, channel)
        if signal == 'DC' and task_key in _active_ao_tasks:
            task_name = _active_ao_tasks[task_key]
            if controller.update_dc_value(task_name, value):
                add_log(
                    f"AO updated: {device.name}/{module.name} DC channel={channel} "
                    f"value={applied_out} {display_unit}"
                )
                return jsonify({
                    'task_name': task_name,
                    'success': True,
                    'updated': True,
                    'applied_value': applied_out,
                    'unit': display_unit,
                    'value_mode': value_mode,
                })

        if task_key in _active_ao_tasks:
            controller.stop_output(_active_ao_tasks[task_key])

        if signal == 'DC':
            task_name = controller.start_dc_output(
                channel, value, output_mode=output_mode
            )
        else:
            task_name = controller.start_ac_output(
                channel, wf_type, frequency, amplitude, output_mode=output_mode
            )

        if task_name:
            _active_ao_tasks[task_key] = task_name
            add_log(f"AO started: {device.name}/{module.name} {signal} channel={channel}")
            return jsonify({
                'task_name': task_name,
                'success': True,
                'value_mode': value_mode,
                'applied_value': applied_out,
                'unit': display_unit,
            })

        return jsonify({'error': 'Failed to start output'}), 500
    except Exception as e:
        log.error("AO start error: %s", e)
        return jsonify({'error': str(e)}), 500


@app.route('/api/ao/stop', methods=['POST'])
def api_ao_stop():
    """API: Stop analog output."""
    try:
        data = request.json
        di, mi = data['device_idx'], data['module_idx']
        channel = data.get('channel')

        device, module, error = get_controller(di, mi)
        if error:
            return jsonify({'error': error}), 400

        controller = AnalogOutputController(task_manager, module)
        if channel:
            task_key = _ao_task_key(di, mi, channel)
            if task_key in _active_ao_tasks:
                controller.stop_output(_active_ao_tasks[task_key])
                del _active_ao_tasks[task_key]
                add_log(f"AO stopped: {channel}")
        else:
            prefix = f"ao_{di}_{mi}_"
            for task_key in list(_active_ao_tasks.keys()):
                if task_key.startswith(prefix):
                    controller.stop_output(_active_ao_tasks[task_key])
                    del _active_ao_tasks[task_key]
            add_log("AO stopped (all channels on module)")

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/di/read', methods=['POST'])
def api_di_read():
    """API: Read digital input."""
    try:
        data = request.json
        di, mi = data['device_idx'], data['module_idx']
        channel = data.get('channel')

        device, module, error = get_controller(di, mi)
        if error:
            return jsonify({'error': error}), 400

        controller = DigitalIOController(task_manager, module)
        channels = [channel] if channel else None
        values = controller.read_digital_input(channels)

        if values:
            if channel:
                v = values.get(channel)
                if v is None:
                    for ch_name, ch_val in values.items():
                        if ch_name.endswith('/' + channel) or ch_name.split('/')[-1] == channel:
                            v = ch_val
                            break
                if v is None:
                    return jsonify({'error': 'Channel not found'}), 404
                formatted = [f"{'HIGH' if v else 'LOW'}"]
                return jsonify({'values': formatted, 'value': v, 'success': True})
            formatted = [
                f"{ch.split('/')[-1]}: {'HIGH' if v else 'LOW'}"
                for ch, v in values.items()
            ]
            add_log(f"DI read: {device.name}/{module.name}")
            return jsonify({'values': formatted, 'success': True})
        
        return jsonify({'error': 'No data'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/do/toggle', methods=['POST'])
def api_do_toggle():
    """API: Toggle digital output."""
    try:
        data = request.json
        di, mi = data['device_idx'], data['module_idx']
        channel = data['channel']
        
        device, module, error = get_controller(di, mi)
        if error:
            return jsonify({'error': error}), 400
        
        controller = DigitalIOController(task_manager, module)
        state = controller.toggle_output(channel)
        
        if state is not None:
            add_log(f"DO toggle: {channel} -> {'HIGH' if state else 'LOW'}")
            return jsonify({'state': state, 'success': True})
        
        return jsonify({'error': 'Toggle failed'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/log')
def api_log():
    """API: Get recent log entries."""
    return jsonify({'log': _log_entries[-50:]})


@app.route('/api/status')
def api_status():
    """API: Get system status summary."""
    devices = device_manager.get_all_devices()
    connected = device_manager.get_connected_devices()
    active_tasks = task_manager.get_active_tasks()
    
    return jsonify({
        'total_devices': len(devices),
        'connected_devices': len(connected),
        'active_tasks': len(active_tasks),
        'active_ai': len(_active_ai_tasks),
        'active_ao': len(_active_ao_tasks),
        'uptime': time.time() - _start_time if '_start_time' in globals() else 0
    })


# ===================== Main Entry Point =====================

_start_time = time.time()

if __name__ == '__main__':
    print()
    print("=" * 60)
    print("  NI DAQ Controller - Web Interface")
    print("=" * 60)
    print()
    print("  Starting server...")
    print()
    
    # Perform initial device discovery with timeout
    add_log("Starting NI DAQ Controller web server...")
    devices = device_manager.discover_devices()
    add_log(f"Detected {len(devices)} device(s)")
    
    print(f"  ✓ Device discovery: {len(devices)} device(s)")
    print(f"  ✓ Web server running on http://localhost:5000")
    print()
    print("  Open in your browser:  http://localhost:5000")
    print()
    print("  Press Ctrl+C to stop")
    print("=" * 60)
    print()
    
    # Run Flask app
    try:
        app.run(
            host='0.0.0.0',
            port=5000,
            debug=False,
            use_reloader=False
        )
    except Exception as e:
        log.error("Server error: %s", e)
        print(f"\n  ERROR: {e}\n")
else:
    # When imported, do initial discovery
    add_log("NI DAQ Controller module loaded")
    try:
        device_manager.discover_devices()
    except Exception as e:
        log.warning("Initial discovery failed: %s", e)
