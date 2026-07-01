"""
Device Manager module for NI DAQ Controller.

Handles automatic discovery and management of NI DAQ devices connected to the
system. Uses NI-DAQmx API to detect devices, enumerate their properties, and
provide real-time status updates.

Supports:
    - Ethernet CompactDAQ chassis (cDAQ-9188, cDAQ-9189, etc.)
    - USB DAQ devices (USB-6009, USB-6210, etc.)
    - PXI DAQ devices
    - PCIe/PCI DAQ devices
    - Multiple simultaneous devices
    - Hot-plug detection via refresh

Typical usage:
    from device_manager import DeviceManager
    dm = DeviceManager()
    devices = dm.discover_devices()
    for device in devices:
        print(device.name, device.product_type)
"""

import time
import socket
import threading
import re
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
from logger import get_logger

log = get_logger(__name__)


class ConnectionType(Enum):
    """Types of physical connections for DAQ devices."""
    ETHERNET = "Ethernet"
    USB = "USB"
    PXI = "PXI"
    PCI = "PCI/PCIe"
    UNKNOWN = "Unknown"


class DeviceStatus(Enum):
    """Operational status of a DAQ device."""
    CONNECTED = "Connected"
    DISCONNECTED = "Disconnected"
    ERROR = "Error"
    UNKNOWN = "Unknown"


@dataclass
class ModuleInfo:
    """
    Information about a module installed in a DAQ chassis.

    Attributes:
        name: Module name (e.g., "cDAQ1Mod1")
        slot_number: Slot number in the chassis
        product_type: Module product type (e.g., "NI 9205")
        serial_number: Module serial number
        supported_operations: List of supported operations (AI, AO, DI, DO, etc.)
        ai_channels: List of analog input channel names
        ao_channels: List of analog output channel names
        di_channels: List of digital input channel names
        do_channels: List of digital output channel names
        ci_channels: List of counter input channel names
        co_channels: List of counter output channel names
        voltage_ranges: Supported voltage ranges for AI/AO
        max_sample_rate: Maximum sample rate in Hz
        supports_continuous: Whether continuous acquisition is supported
    """
    name: str
    slot_number: int
    product_type: str = ""
    serial_number: str = ""
    supported_operations: List[str] = field(default_factory=list)
    ai_channels: List[str] = field(default_factory=list)
    ao_channels: List[str] = field(default_factory=list)
    di_channels: List[str] = field(default_factory=list)
    do_channels: List[str] = field(default_factory=list)
    ci_channels: List[str] = field(default_factory=list)
    co_channels: List[str] = field(default_factory=list)
    voltage_ranges: List[Tuple[float, float]] = field(default_factory=list)
    max_sample_rate: float = 0.0
    supports_continuous: bool = False
    is_simulated: bool = False


@dataclass
class DeviceInfo:
    """
    Comprehensive information about a discovered DAQ device.

    Attributes:
        name: Device name as reported by NI-DAQmx
        product_type: Product type/model name
        serial_number: Device serial number
        connection_type: Physical connection type (Ethernet, USB, PXI, PCI)
        ip_address: IP address if Ethernet connection
        status: Current connection status
        modules: List of installed modules/ subsystems
        ai_channels: All analog input channels
        ao_channels: All analog output channels
        di_channels: All digital input channels
        do_channels: All digital output channels
        ci_channels: All counter input channels
        co_channels: All counter output channels
        max_ai_rate: Maximum AI sample rate
        max_ao_rate: Maximum AO sample rate
        is_simulated: Whether this is a simulated device
    """
    name: str
    product_type: str = ""
    serial_number: str = ""
    connection_type: ConnectionType = ConnectionType.UNKNOWN
    ip_address: str = ""
    status: DeviceStatus = DeviceStatus.UNKNOWN
    modules: List[ModuleInfo] = field(default_factory=list)
    ai_channels: List[str] = field(default_factory=list)
    ao_channels: List[str] = field(default_factory=list)
    di_channels: List[str] = field(default_factory=list)
    do_channels: List[str] = field(default_factory=list)
    ci_channels: List[str] = field(default_factory=list)
    co_channels: List[str] = field(default_factory=list)
    max_ai_rate: float = 0.0
    max_ao_rate: float = 0.0
    is_simulated: bool = False
    last_discovered: float = field(default_factory=time.time)


class DeviceManager:
    """
    Manages discovery and monitoring of NI DAQ devices.

    Provides methods to enumerate connected devices, retrieve detailed
    device information, detect modules in chassis, and monitor device
    connection status.

    Attributes:
        _devices: Dictionary of discovered devices by name
        _lock: Thread lock for safe concurrent access
        _nidaqmx: Reference to the nidaqmx module
        _initialized: Whether nidaqmx has been loaded
        _refresh_callbacks: Callbacks for device changes
    """

    def __init__(self) -> None:
        """Initialize the device manager."""
        self._devices: Dict[str, DeviceInfo] = {}
        self._lock = threading.Lock()
        self._nidaqmx = None
        self._nidaqmx_system = None
        self._initialized = False
        self._refresh_callbacks: List[callable] = []
        self._last_discovery_error: str = ""

        log.info("DeviceManager initialized")

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
            self._nidaqmx_system = nidaqmx.system.System.local()
            self._initialized = True
            log.debug("NI-DAQmx library loaded successfully")
            return True
        except ImportError:
            log.error("NI-DAQmx library not installed. Install with: pip install nidaqmx")
            return False
        except Exception as e:
            log.error("Failed to initialize NI-DAQmx: %s", e)
            return False

    def discover_devices(self) -> List[DeviceInfo]:
        """
        Discover all NI DAQ devices visible to the local NI-DAQmx driver.

        Same model as a direct ``System.local().devices`` scan: USB, PCI, and
        Ethernet chassis already registered in NI MAX appear automatically.

        Returns:
            List of DeviceInfo objects for all discovered devices
        """
        if not self._check_nidaqmx():
            with self._lock:
                return list(self._devices.values())

        discovered: Dict[str, DeviceInfo] = {}
        self._last_discovery_error = ""

        try:
            daq_devices = list(self._nidaqmx_system.devices)
            log.info("Discovering NI DAQ devices... (%d found)", len(daq_devices))

            for device in daq_devices:
                try:
                    device_info = self._get_device_info(device)
                    if device_info:
                        discovered[device_info.name] = device_info
                        log.debug(
                            "Discovered device: %s (%s) - %s",
                            device_info.name,
                            device_info.product_type,
                            device_info.connection_type.value,
                        )

                except Exception as e:
                    log.warning(
                        "Error discovering device '%s': %s",
                        getattr(device, 'name', 'unknown'),
                        e,
                    )

            discovered = self._group_chassis_modules(discovered)

            with self._lock:
                for name in list(self._devices.keys()):
                    if name not in discovered:
                        self._devices[name].status = DeviceStatus.DISCONNECTED
                        log.warning("Device '%s' is no longer connected", name)
                self._devices.update(discovered)

            log.info("Device discovery complete. %d device(s) found.", len(discovered))
            self._notify_refresh_callbacks()
            return list(discovered.values())

        except Exception as e:
            self._last_discovery_error = str(e)
            log.warning("NI-DAQmx device scan failed: %s", e)
            with self._lock:
                return list(self._devices.values())

    def _group_chassis_modules(
        self, discovered: Dict[str, DeviceInfo]
    ) -> Dict[str, DeviceInfo]:
        """
        Merge Ethernet cDAQ module devices into their parent chassis.

        NI-DAQmx often lists ``cDAQxxxxMod1``, ``cDAQxxxxMod3`` as separate
        entries alongside the chassis. The UI expects one chassis with modules.
        """
        mod_re = re.compile(r'^(.+)(Mod\d+)$')
        to_remove: List[str] = []

        for name, mod_device in discovered.items():
            match = mod_re.match(name)
            if not match:
                continue

            chassis_name = match.group(1)
            if chassis_name not in discovered:
                continue

            slot_num = int(match.group(2).replace('Mod', ''))
            chassis = discovered[chassis_name]

            if mod_device.modules:
                module = mod_device.modules[0]
                module.name = name
                module.slot_number = slot_num
                module.product_type = mod_device.product_type or module.product_type
                if mod_device.ai_channels:
                    module.ai_channels = list(mod_device.ai_channels)
                if mod_device.ao_channels:
                    module.ao_channels = list(mod_device.ao_channels)
                if mod_device.di_channels:
                    module.di_channels = list(mod_device.di_channels)
                if mod_device.do_channels:
                    module.do_channels = list(mod_device.do_channels)
                module.supported_operations = [
                    op for op, chs in (
                        ('AI', module.ai_channels),
                        ('AO', module.ao_channels),
                        ('DI', module.di_channels),
                        ('DO', module.do_channels),
                        ('CI', module.ci_channels),
                        ('CO', module.co_channels),
                    ) if chs
                ]
            else:
                module = ModuleInfo(
                    name=name,
                    slot_number=slot_num,
                    product_type=mod_device.product_type,
                    serial_number=mod_device.serial_number,
                    supported_operations=[],
                    ai_channels=list(mod_device.ai_channels),
                    ao_channels=list(mod_device.ao_channels),
                    di_channels=list(mod_device.di_channels),
                    do_channels=list(mod_device.do_channels),
                    ci_channels=list(mod_device.ci_channels),
                    co_channels=list(mod_device.co_channels),
                    max_sample_rate=mod_device.max_ai_rate,
                    supports_continuous=len(mod_device.ai_channels) > 0,
                    is_simulated=mod_device.is_simulated,
                )
                for prefix, channels in (
                    ('AI', module.ai_channels),
                    ('AO', module.ao_channels),
                    ('DI', module.di_channels),
                    ('DO', module.do_channels),
                    ('CI', module.ci_channels),
                    ('CO', module.co_channels),
                ):
                    if channels:
                        module.supported_operations.append(prefix)

            chassis.modules = [
                m for m in chassis.modules
                if m.supported_operations or m.name != chassis_name
            ]
            chassis.modules.append(module)
            chassis.modules.sort(key=lambda m: m.slot_number)

            for ch_list, mod_list in (
                (chassis.ai_channels, module.ai_channels),
                (chassis.ao_channels, module.ao_channels),
                (chassis.di_channels, module.di_channels),
                (chassis.do_channels, module.do_channels),
                (chassis.ci_channels, module.ci_channels),
                (chassis.co_channels, module.co_channels),
            ):
                for ch in mod_list:
                    if ch not in ch_list:
                        ch_list.append(ch)

            if module.max_sample_rate > chassis.max_ai_rate:
                chassis.max_ai_rate = module.max_sample_rate

            to_remove.append(name)

        for name in to_remove:
            discovered.pop(name, None)

        if to_remove:
            log.info(
                "Grouped %d cDAQ module device(s) under chassis entries",
                len(to_remove),
            )

        return discovered

    def _get_device_info(self, device: Any) -> Optional[DeviceInfo]:
        """
        Extract detailed information from a NI-DAQmx device object.

        Args:
            device: NI-DAQmx device object

        Returns:
            DeviceInfo object with extracted information, or None on error
        """
        try:
            name = device.name
            product_type = getattr(device, 'product_type', '') or ''
            serial_raw = getattr(device, 'serial_num', None)
            if serial_raw is None:
                serial_raw = getattr(device, 'serial_number', '')
            try:
                serial_number = hex(serial_raw) if isinstance(serial_raw, int) else str(serial_raw)
            except (TypeError, ValueError):
                serial_number = str(serial_raw)

            connection_type = self._detect_connection_type(device)
            ip_address = self._get_ip_address(device)

            # Get device status
            status = DeviceStatus.CONNECTED

            # Check if simulated
            is_simulated = getattr(device, 'is_simulated', False)

            # Create DeviceInfo
            device_info = DeviceInfo(
                name=name,
                product_type=product_type,
                serial_number=str(serial_number),
                connection_type=connection_type,
                ip_address=ip_address,
                status=status,
                is_simulated=is_simulated,
                modules=[]
            )

            # Detect modules (for chassis devices)
            modules = self._detect_modules(device)
            device_info.modules = modules

            # Aggregate channels from modules
            for module in modules:
                device_info.ai_channels.extend(module.ai_channels)
                device_info.ao_channels.extend(module.ao_channels)
                device_info.di_channels.extend(module.di_channels)
                device_info.do_channels.extend(module.do_channels)
                device_info.ci_channels.extend(module.ci_channels)
                device_info.co_channels.extend(module.co_channels)

                if module.max_sample_rate > device_info.max_ai_rate:
                    device_info.max_ai_rate = module.max_sample_rate

            # Also try to get device-level channel information
            self._add_device_level_channels(device, device_info)

            return device_info

        except Exception as e:
            log.error("Error getting device info for '%s': %s",
                      getattr(device, 'name', 'unknown'), e)
            return None

    def _detect_connection_type(self, device: Any) -> ConnectionType:
        """Detect connection type from NI-DAQmx device properties."""
        try:
            ip = getattr(device, 'tcpip_ethernet_ip', None)
            if ip and str(ip) not in ('0.0.0.0', ''):
                return ConnectionType.ETHERNET
        except Exception:
            pass

        name_lower = device.name.lower()
        if any(p in name_lower for p in ('cdaq', 'eth', 'tcp', 'network')):
            return ConnectionType.ETHERNET
        if 'pxi' in name_lower:
            return ConnectionType.PXI
        if 'usb' in name_lower:
            return ConnectionType.USB
        if any(p in name_lower for p in ('pci', 'pcie')):
            return ConnectionType.PCI

        try:
            bus_type = getattr(device, 'bus_type', None)
            if bus_type is not None:
                bus_str = str(bus_type).lower()
                if 'usb' in bus_str:
                    return ConnectionType.USB
                if 'pci' in bus_str:
                    return ConnectionType.PCI
                if 'pxi' in bus_str:
                    return ConnectionType.PXI
                if 'tcp' in bus_str or 'ethernet' in bus_str:
                    return ConnectionType.ETHERNET
        except Exception:
            pass

        return ConnectionType.UNKNOWN

    def _get_ip_address(self, device: Any) -> str:
        """Read IP/hostname from NI-DAQmx device properties only."""
        try:
            ip = getattr(device, 'tcpip_ethernet_ip', None)
            if ip and str(ip) not in ('0.0.0.0', ''):
                return str(ip)
        except Exception:
            pass

        try:
            hostname = getattr(device, 'tcpip_hostname', None)
            if hostname:
                return str(hostname)
        except Exception:
            pass

        return ""

    def _detect_modules(self, device: Any) -> List[ModuleInfo]:
        """
        Detect modules installed in a chassis device.

        Args:
            device: NI-DAQmx device object

        Returns:
            List of ModuleInfo objects
        """
        modules: List[ModuleInfo] = []

        try:
            # Try to get modules from device
            if hasattr(device, 'modules'):
                daq_modules = device.modules
                for module in daq_modules:
                    try:
                        module_info = self._get_module_info(module, device.name)
                        modules.append(module_info)
                    except Exception as e:
                        log.warning("Error detecting module: %s", e)

        except Exception as e:
            log.debug("No modules found for device '%s': %s",
                      device.name, e)

        # If no modules found, treat the device itself as a single module
        if not modules:
            module_info = self._create_device_as_module(device)
            modules.append(module_info)

        # Sort by slot number
        modules.sort(key=lambda m: m.slot_number)

        return modules

    def _get_module_info(self, module: Any,
                         device_name: str) -> ModuleInfo:
        """
        Extract information from a chassis module.

        Args:
            module: NI-DAQmx module object
            device_name: Parent device name

        Returns:
            ModuleInfo object
        """
        module_name = module.name
        slot_number = getattr(module, 'slot_number', 0)
        product_type = getattr(module, 'product_type', '')
        serial_number = str(getattr(module, 'serial_number', ''))
        is_simulated = getattr(module, 'is_simulated', False)

        # Get supported operations and channels
        ai_channels = self._get_module_channels(module, 'ai')
        ao_channels = self._get_module_channels(module, 'ao')
        di_channels = self._get_module_channels(module, 'di')
        do_channels = self._get_module_channels(module, 'do')
        ci_channels = self._get_module_channels(module, 'ci')
        co_channels = self._get_module_channels(module, 'co')

        # Build supported operations
        supported_ops = []
        if ai_channels:
            supported_ops.append("AI")
        if ao_channels:
            supported_ops.append("AO")
        if di_channels:
            supported_ops.append("DI")
        if do_channels:
            supported_ops.append("DO")
        if ci_channels:
            supported_ops.append("CI")
        if co_channels:
            supported_ops.append("CO")

        # Get voltage ranges
        voltage_ranges = self._get_voltage_ranges(module)

        # Get max sample rate
        max_sample_rate = getattr(module, 'ai_max_rate', 0.0) or \
                          getattr(module, 'ao_max_rate', 0.0)

        return ModuleInfo(
            name=module_name,
            slot_number=slot_number,
            product_type=product_type,
            serial_number=serial_number,
            supported_operations=supported_ops,
            ai_channels=ai_channels,
            ao_channels=ao_channels,
            di_channels=di_channels,
            do_channels=do_channels,
            ci_channels=ci_channels,
            co_channels=co_channels,
            voltage_ranges=voltage_ranges,
            max_sample_rate=float(max_sample_rate),
            supports_continuous=len(ai_channels) > 0,
            is_simulated=is_simulated
        )

    def _create_device_as_module(self, device: Any) -> ModuleInfo:
        """
        Create a ModuleInfo for a device that has no sub-modules.

        Args:
            device: NI-DAQmx device object

        Returns:
            ModuleInfo representing the entire device
        """
        # Get channels
        ai_channels = self._get_device_channels(device, 'ai')
        ao_channels = self._get_device_channels(device, 'ao')
        di_channels = self._get_device_channels(device, 'di')
        do_channels = self._get_device_channels(device, 'do')
        ci_channels = self._get_device_channels(device, 'ci')
        co_channels = self._get_device_channels(device, 'co')

        supported_ops = []
        if ai_channels:
            supported_ops.append("AI")
        if ao_channels:
            supported_ops.append("AO")
        if di_channels:
            supported_ops.append("DI")
        if do_channels:
            supported_ops.append("DO")
        if ci_channels:
            supported_ops.append("CI")
        if co_channels:
            supported_ops.append("CO")

        return ModuleInfo(
            name=device.name,
            slot_number=0,
            product_type=getattr(device, 'product_type', ''),
            serial_number=str(getattr(device, 'serial_number', '')),
            supported_operations=supported_ops,
            ai_channels=ai_channels,
            ao_channels=ao_channels,
            di_channels=di_channels,
            do_channels=do_channels,
            ci_channels=ci_channels,
            co_channels=co_channels,
            max_sample_rate=float(getattr(device, 'ai_max_rate', 0) or
                                  getattr(device, 'ao_max_rate', 0)),
            supports_continuous=len(ai_channels) > 0,
            is_simulated=getattr(device, 'is_simulated', False)
        )

    def _get_module_channels(self, module: Any,
                             prefix: str) -> List[str]:
        """
        Get channel names for a specific type from a module.

        Args:
            module: NI-DAQmx module object
            prefix: Channel prefix ('ai', 'ao', 'di', 'do', 'ci', 'co')

        Returns:
            List of channel names
        """
        try:
            channel_names = []
            module_channel_attrs = {
                'ai': ['ai_physical_chans', 'ai_channels', 'ai_phys_chans',
                       'ai_physical_channels'],
                'ao': ['ao_physical_chans', 'ao_channels', 'ao_phys_chans',
                       'ao_physical_channels'],
                'di': ['di_lines', 'di_channels', 'di_phys_chans',
                       'di_physical_channels'],
                'do': ['do_lines', 'do_channels', 'do_phys_chans',
                       'do_physical_channels'],
                'ci': ['ci_physical_chans', 'ci_channels', 'ci_phys_chans',
                       'ci_physical_channels'],
                'co': ['co_physical_chans', 'co_channels', 'co_phys_chans',
                       'co_physical_channels'],
            }

            for attr_name in module_channel_attrs.get(prefix, [f'{prefix}_channels']):
                if hasattr(module, attr_name):
                    channels = getattr(module, attr_name)
                    if channels:
                        for ch in list(channels):
                            channel_names.append(self._channel_name(ch))
                        break

            return channel_names

        except Exception as e:
            log.debug("Error getting %s channels for module: %s",
                      prefix, e)
            return []

    def _channel_name(self, ch: Any) -> str:
        """Physical channel name as reported by NI-DAQmx."""
        return str(getattr(ch, 'name', ch))

    def _get_device_channels(self, device: Any, prefix: str) -> List[str]:
        """Enumerate channels on a device (same attributes as NI-DAQmx Python API)."""
        attr_map = {
            'ai': 'ai_physical_chans',
            'ao': 'ao_physical_chans',
            'di': 'di_lines',
            'do': 'do_lines',
            'ci': 'ci_physical_chans',
            'co': 'co_physical_chans',
        }
        attr = attr_map.get(prefix)
        if not attr or not hasattr(device, attr):
            return []
        try:
            return [self._channel_name(ch) for ch in getattr(device, attr)]
        except Exception as e:
            log.debug("Error getting %s channels for device: %s", prefix, e)
            return []

    def _get_voltage_ranges(self, module: Any) -> List[Tuple[float, float]]:
        """
        Get supported voltage ranges from a module.

        Args:
            module: NI-DAQmx module object

        Returns:
            List of (min, max) voltage range tuples
        """
        ranges = [(-10.0, 10.0)]  # Default range

        try:
            if hasattr(module, 'ai_voltage_ranges'):
                ranges = [(r.min, r.max) for r in module.ai_voltage_ranges]
            elif hasattr(module, 'ao_voltage_ranges'):
                ranges = [(r.min, r.max) for r in module.ao_voltage_ranges]
        except Exception:
            pass

        return ranges

    def _add_device_level_channels(self, device: Any,
                                    device_info: DeviceInfo) -> None:
        """
        Add device-level channel information not already captured from modules.

        Args:
            device: NI-DAQmx device object
            device_info: DeviceInfo to update
        """
        prefixes = ['ai', 'ao', 'di', 'do', 'ci', 'co']
        channel_attrs = {
            'ai': 'ai_physical_chans',
            'ao': 'ao_physical_chans',
            'di': 'di_lines',
            'do': 'do_lines',
            'ci': 'ci_physical_chans',
            'co': 'co_physical_chans'
        }

        for prefix, attr in channel_attrs.items():
            try:
                if hasattr(device, attr):
                    channels = getattr(device, attr)
                    existing = getattr(device_info, f'{prefix}_channels')
                    for ch in channels:
                        ch_name = str(ch)
                        if ch_name not in existing:
                            existing.append(ch_name)
            except Exception:
                pass

    def get_device(self, name: str) -> Optional[DeviceInfo]:
        """
        Get information about a specific device.

        Args:
            name: Device name

        Returns:
            DeviceInfo if found, None otherwise
        """
        with self._lock:
            return self._devices.get(name)

    def get_all_devices(self) -> List[DeviceInfo]:
        """
        Get information about all discovered devices.

        Returns:
            List of all DeviceInfo objects
        """
        with self._lock:
            return list(self._devices.values())

    def get_connected_devices(self) -> List[DeviceInfo]:
        """
        Get information about currently connected devices only.

        Returns:
            List of DeviceInfo objects for connected devices
        """
        with self._lock:
            return [
                d for d in self._devices.values()
                if d.status == DeviceStatus.CONNECTED
            ]

    def refresh_devices(self) -> List[DeviceInfo]:
        """
        Force a refresh of device discovery.

        This rescans all connected hardware and updates the device list.
        Useful for detecting newly connected or removed devices.

        Returns:
            Updated list of DeviceInfo objects
        """
        log.info("Manual device refresh initiated")
        return self.discover_devices()

    def add_network_device(self,
                           ip_or_hostname: str,
                           device_name: str = '',
                           attempt_reservation: bool = True,
                           timeout: float = 10.0) -> str:
        """
        Register an Ethernet cDAQ chassis with NI-DAQmx (user-initiated).

        After a successful add, the device appears in ``System.local().devices``
        and is picked up by :meth:`discover_devices`. If ``device_name`` is
        omitted, NI-DAQmx assigns the name.

        Args:
            ip_or_hostname: IP address or hostname entered by the user
            device_name: Optional DAQmx name (leave empty for auto-assignment)
            attempt_reservation: Whether to reserve the device for this PC
            timeout: Timeout in seconds for the add operation

        Returns:
            DAQmx device name assigned by the driver
        """
        if not self._check_nidaqmx():
            raise RuntimeError("NI-DAQmx is not available")

        kwargs: Dict[str, Any] = {
            'attempt_reservation': attempt_reservation,
            'timeout': timeout,
        }
        if device_name and device_name.strip():
            kwargs['device_name'] = device_name.strip()

        added = self._nidaqmx.system.Device.add_network_device(
            ip_or_hostname.strip(),
            **kwargs,
        )
        log.info("Added network device '%s' at %s", added.name, ip_or_hostname)
        return added.name

    def get_last_discovery_error(self) -> str:
        """Return the last NI-DAQmx scan error message, if any."""
        return self._last_discovery_error

    def get_device_summary(self) -> List[Dict[str, Any]]:
        """
        Get a summary of all devices for display on the dashboard.

        Returns:
            List of dictionaries with device summary information
        """
        devices = self.get_all_devices()
        summary = []

        for device in devices:
            module_count = len(device.modules)
            total_channels = (
                len(device.ai_channels) +
                len(device.ao_channels) +
                len(device.di_channels) +
                len(device.do_channels)
            )

            summary.append({
                'name': device.name,
                'product_type': device.product_type,
                'connection_type': device.connection_type.value,
                'ip_address': device.ip_address,
                'status': device.status.value,
                'module_count': module_count,
                'total_channels': total_channels,
                'ai_channels': len(device.ai_channels),
                'ao_channels': len(device.ao_channels),
                'di_channels': len(device.di_channels),
                'do_channels': len(device.do_channels),
                'is_simulated': device.is_simulated
            })

        return summary

    def register_refresh_callback(self, callback: callable) -> None:
        """
        Register a callback to be called when devices are refreshed.

        Args:
            callback: Function to call on device refresh
        """
        if callback not in self._refresh_callbacks:
            self._refresh_callbacks.append(callback)

    def unregister_refresh_callback(self, callback: callable) -> None:
        """
        Unregister a previously registered refresh callback.

        Args:
            callback: Callback to remove
        """
        if callback in self._refresh_callbacks:
            self._refresh_callbacks.remove(callback)

    def _notify_refresh_callbacks(self) -> None:
        """Notify all registered callbacks of a device refresh."""
        for callback in self._refresh_callbacks:
            try:
                callback()
            except Exception as e:
                log.warning("Error in refresh callback: %s", e)

    def cleanup(self) -> None:
        """Clean up resources used by the device manager."""
        with self._lock:
            self._devices.clear()
            self._refresh_callbacks.clear()
        log.info("DeviceManager cleaned up")