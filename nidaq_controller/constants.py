"""
Constants and configuration defaults for ATS Test System.
Centralizes all magic numbers and hardcoded values for easy maintenance.
"""

# Network/Server
DEFAULT_HOST = '0.0.0.0'
DEFAULT_PORT = 5000
NI_DAQMX_DISCOVERY_PORT = 3580

# Timeouts (seconds)
ETHERNET_HOST_REACHABLE_TIMEOUT = 1.5
NI_DAQMX_PROBE_TIMEOUT = 2.0
THREAD_JOIN_TIMEOUT = 1.0
NETWORK_DEVICE_ADD_TIMEOUT = 10.0
DEFAULT_READ_TIMEOUT = 10.0

# Buffers and Limits
MAX_LOG_ENTRIES = 200
AC_WAVEFORM_SAMPLES = 5000
AC_SAMPLE_RATE = 10000.0
DEFAULT_BUFFER_SIZE = 1000

# Default Values
DEFAULT_SAMPLE_RATE = 1000.0
MAX_SAMPLE_RATE = 1000000.0
DEFAULT_SAMPLES_PER_CHANNEL = 100
DEFAULT_VOLTAGE_RANGE = (-10.0, 10.0)
DEFAULT_CURRENT_RANGE = (0.0, 0.02)
DEFAULT_FREQUENCY = 50.0
MAX_FREQUENCY = 10000.0
DEFAULT_REFRESH_RATE_MS = 100
MAX_REFRESH_RATE_MS = 1000
MIN_REFRESH_RATE_MS = 10

# Logging
DEFAULT_LOG_LEVEL = 'INFO'
MAX_LOG_FILE_SIZE_MB = 10
LOG_BACKUP_COUNT = 5

# Current/Voltage Conversion
MILLIAMPS_TO_AMPS = 1000.0

# Digital I/O
DEFAULT_MONITOR_INTERVAL_MS = 100.0

# Breaker trip record on 4 wet digital inputs.
# Pre-trigger, 10 ms trip pulse, 52A falls with the pulse, 52B rises
# opening_travel later, close coil stays low, then post-trigger.
TRIP_PRE_TRIGGER_S = 0.200
TRIP_PULSE_S = 0.010
TRIP_OPENING_TRAVEL_S = 0.100
TRIP_POST_TRIGGER_S = 0.300
TRIP_OPERATION_MAX_S = 3.0
TRIP_WAVEFORM_RATE_HZ = 1000.0
TRIP_ARM_TIMEOUT_S = 20.0
# Gap between the end of a trip record and the close arm, and again
# between close and the next trip.
OPERATION_INTERVAL_S = 30.0