"""One-shot iph fault current plus the wet trip sequence.

iph on NI-9264 ao0 is a single finite buffer: 2 s of 50 Hz sine at 30% of
nominal, then one 90% quarter-cycle, then zero. The 30% to 90% join is the
positive-going zero crossing of that same sine. Nothing is added as a DC
offset, and no zero samples are inserted between the two amplitudes.

Before the fault the breaker is closed on every line. 9266 currents are
0 mA (tc1, tc2, cc, imtr). Trip coil, backup trip, 52B, and close are low.
52A is high. At the fault, tc1 is 20 mA for 5 ms and the wet trip runs:
10 ms trip-coil pulse, 52A falls on that falling edge, 52B rises 100 ms
later. Close and backup stay low. ``play_breaker_operation`` holds the
closed state for its pre-trigger before the coil rises, so that call starts
one pre-trigger before the fault. The coil itself rises at the fault.

This module is not called from the main MV BCM run. The Fault current
button is the caller until the sequence is confirmed on the bench.
"""

from __future__ import annotations

import math
import threading
import time
from datetime import datetime
from typing import Any

import numpy as np

from nidaq_controller.constants import (
    AC_SAMPLE_RATE,
    DEFAULT_VOLTAGE_RANGE,
    TRIP_PRE_TRIGGER_S,
)
from nidaq_controller.mvbcm.breaker_ops import (
    MvbcmStopped,
    _ensure_daq_path,
    _raise_if_stopped,
    default_trip_channels,
    device_name_from_channels,
    find_digital_module,
    open_breaker_controller,
    resolve_channels,
)
from nidaq_controller.mvbcm.channel_map import GROUPS, MappedChannel
from nidaq_controller.mvbcm.live import LiveError, fetch_live, in_limits, measure
from nidaq_controller.mvbcm.stimuli.current import CurrentStimulus, find_9266
from nidaq_controller.mvbcm.stimuli.voltage import find_9264

# Nominal iph full scale: 20 A RMS = 333 mV RMS at the board. The fixture
# divides the 9264 by 11, so the card is driven at 11 times that voltage.
IPH_FS_MV = 333.0
IPH_FS_A = 20.0
IPH_SCALE = 11.0
PRE_FRACTION = 0.30
FAULT_FRACTION = 0.90
PRE_S = 2.0
FAULT_S = 0.005
TAIL_S = 0.5
FREQUENCY_HZ = 50.0
SAMPLE_RATE = AC_SAMPLE_RATE
# ±5% of 20 A. The sweep window (±0.2 A) is too tight for this peak.
PEAK_TOL_A = 1.0
TC1_ON_MA = 20.0
TC1_PULSE_S = 0.005
SCORE_WINDOW_S = 4.0
_LIVE_POLL_S = 0.5

_IPH = "ao0"
# Closed breaker, before the trip command.
_CLOSED_DIGITAL = {
    "trip_coil": False,
    "backup_trip_coil": False,
    "switch_52a": True,
    "switch_52b": False,
    "close_coil": False,
}


def _current_channels() -> list[MappedChannel]:
    """Every 9266 current in the map: tc1, tc2, cc, imtr."""
    for group in GROUPS:
        if group.id == "tc_current":
            return list(group.channels)
    raise RuntimeError("tc_current group is not configured")


def _milliamp_levels(channels: list[MappedChannel], tc1_ma: float) -> dict[str, float]:
    """All currents off, except tc1 when the trip command is on."""
    levels = {channel.signal: 0.0 for channel in channels}
    levels["tc1"] = tc1_ma
    return levels


def card_peak_volts(fraction: float) -> float:
    """9264 volts peak for a fraction of nominal iph."""
    rms_volts = (fraction * IPH_FS_MV / 1000.0) * IPH_SCALE
    return rms_volts * math.sqrt(2.0)


def expected_fault_peak_a() -> float:
    """Engineering amps at the top of the 90% quarter-cycle."""
    return FAULT_FRACTION * IPH_FS_A * math.sqrt(2.0)


def build_iph_fault_wave() -> np.ndarray:
    """One buffer: 30% sine, 90% quarter-cycle, then zeros.

    Sample ``pre_n`` is the positive-going zero crossing. Amplitude changes
    there, where the sine value is already zero, so the join has no step
    and no inserted gap.
    """
    if FREQUENCY_HZ <= 0 or SAMPLE_RATE <= 0:
        raise RuntimeError("sample rate and frequency must be positive")
    samples_per_cycle = SAMPLE_RATE / FREQUENCY_HZ
    if samples_per_cycle != int(samples_per_cycle):
        raise RuntimeError(
            f"{SAMPLE_RATE:g} Hz does not land {FREQUENCY_HZ:g} Hz on a sample"
        )
    samples_per_cycle = int(samples_per_cycle)
    pre_n = int(round(PRE_S * SAMPLE_RATE))
    fault_n = int(round(FAULT_S * SAMPLE_RATE))
    tail_n = int(round(TAIL_S * SAMPLE_RATE))
    if pre_n % samples_per_cycle != 0:
        raise RuntimeError("pre-fault length is not an integer number of cycles")
    if fault_n != samples_per_cycle // 4:
        raise RuntimeError("fault length is not a quarter-cycle")
    if tail_n < 1:
        raise RuntimeError("zero tail is empty")

    peak_30 = card_peak_volts(PRE_FRACTION)
    peak_90 = card_peak_volts(FAULT_FRACTION)
    low, high = DEFAULT_VOLTAGE_RANGE
    for label, peak in (("30%", peak_30), ("90%", peak_90)):
        if peak < low or peak > high:
            raise RuntimeError(
                f"iph {label} is {peak:.3f} V peak, outside [{low:g}, {high:g}] V"
            )

    total = pre_n + fault_n + tail_n + 1
    index = np.arange(total, dtype=np.float64)
    sine = np.sin(2.0 * math.pi * FREQUENCY_HZ * index / SAMPLE_RATE)
    amplitude = np.empty(total, dtype=np.float64)
    amplitude[:pre_n] = peak_30
    amplitude[pre_n:pre_n + fault_n + 1] = peak_90
    amplitude[pre_n + fault_n + 1:] = 0.0
    wave = amplitude * sine
    _require_clean_join(wave, pre_n, fault_n, peak_30, peak_90)
    return wave


def _require_clean_join(
    wave: np.ndarray,
    pre_n: int,
    fault_n: int,
    peak_30: float,
    peak_90: float,
) -> None:
    """Reject a buffer that steps, sits at zero, or carries a DC offset."""
    if abs(float(wave[pre_n])) > 1e-6:
        raise RuntimeError("fault does not start on a zero crossing")
    if float(wave[pre_n - 1]) >= 0.0:
        raise RuntimeError("pre-fault sine does not approach the crossing from below")
    rise_end = pre_n + max(2, fault_n // 4)
    rise = wave[pre_n + 1:rise_end]
    if rise.size == 0 or float(rise[0]) <= 0.0 or np.any(np.abs(rise) < 1e-4):
        raise RuntimeError("30% to 90% transition holds at zero")
    pre = wave[:pre_n]
    if abs(float(np.mean(pre))) > 1e-3:
        raise RuntimeError("pre-fault sine has a DC offset")
    fault = wave[pre_n:pre_n + fault_n + 1]
    if abs(float(np.max(fault)) - peak_90) > 1e-4:
        raise RuntimeError("fault quarter-cycle does not reach the 90% peak")
    if np.any(wave[pre_n + fault_n + 1:] != 0.0):
        raise RuntimeError("hold after the fault is not zero")
    # Largest legal step is the 90% slope across one sample.
    max_step = peak_90 * 2.0 * math.pi * FREQUENCY_HZ / SAMPLE_RATE * 1.05
    steps = np.abs(np.diff(wave[:pre_n + fault_n + 1]))
    if float(np.max(steps)) > max_step:
        raise RuntimeError("iph buffer has a step between samples")
    # The sample before the crossing is still the 30% sine, not a DC pedestal.
    expected_prev = peak_30 * math.sin(
        2.0 * math.pi * FREQUENCY_HZ * (pre_n - 1) / SAMPLE_RATE
    )
    if abs(float(wave[pre_n - 1]) - expected_prev) > 1e-6:
        raise RuntimeError("sample before the fault is not the 30% sine")


class _FiniteIph:
    """Play one voltage buffer once, then hold its last sample."""

    def __init__(self, device_name: str, samples: np.ndarray):
        import nidaqmx

        if not device_name:
            raise ValueError("iph fault has no 9264 device")
        physical = _IPH if "/" in _IPH else f"{device_name}/{_IPH}"
        low, high = DEFAULT_VOLTAGE_RANGE
        self._task = nidaqmx.Task()
        self._started = False
        self._samples = int(len(samples))
        try:
            self._task.ao_channels.add_ao_voltage_chan(
                physical,
                min_val=low,
                max_val=high,
            )
            self._task.timing.cfg_samp_clk_timing(
                rate=SAMPLE_RATE,
                sample_mode=nidaqmx.constants.AcquisitionType.FINITE,
                samps_per_chan=self._samples,
            )
            self._task.write(np.asarray(samples, dtype=np.float64), auto_start=False)
        except Exception as exc:
            self._task.close()
            self._task = None
            raise RuntimeError(
                f"Could not arm iph on {device_name}: {exc}. "
                "Stop any analog output already running on this module."
            ) from exc

    def start(self) -> float:
        self._task.start()
        self._started = True
        return time.perf_counter()

    def wait(self, timeout: float) -> None:
        if self._task is not None and self._started:
            self._task.wait_until_done(timeout=timeout)

    def close(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        try:
            if self._started:
                try:
                    task.stop()
                except Exception:
                    pass
        finally:
            task.close()
            self._started = False


def run_fault_current(
    board: str,
    *,
    voltage_device: str | None = None,
    current_device: str | None = None,
    channels: dict[str, str] | None = None,
    module=None,
    controller_factory=None,
    fetch=None,
    on_row=None,
    should_stop=None,
    timeout: float = 5.0,
) -> list[dict]:
    """Play the fault record, pulse tc1, trip, and score iph ``peak_eng``."""
    if fetch is None:
        def fetch(url, _timeout=timeout):
            return fetch_live(url, timeout=_timeout)

    wave = build_iph_fault_wave()
    expect = expected_fault_peak_a()
    low = expect - PEAK_TOL_A
    high = expect + PEAK_TOL_A
    vdev = voltage_device or find_9264()
    cdev = current_device or find_9266()
    resolved, controller = _open_trip(channels, module, controller_factory)

    voltage: _FiniteIph | None = None
    current: CurrentStimulus | None = None
    trip_thread: threading.Thread | None = None
    stop_event = threading.Event()
    trip_box: dict[str, Any] = {}
    try:
        current_channels = _current_channels()
        voltage = _FiniteIph(vdev, wave)
        current = CurrentStimulus(cdev, current_channels)
        current.write(_milliamp_levels(current_channels, 0.0))
        # Closed state before the waveform starts: 52A high, 52B low,
        # trip coil low, close low, backup low. The shared trip player
        # pulses backup when that line is in the map; this sequence keeps
        # backup low, so the timed call does not include it.
        # That player also rewrites the closed state before its 200 ms
        # pre-trigger, which would delay the coil. Shift the call earlier
        # by the time this first write took.
        prime_started = time.perf_counter()
        _prime_breaker_closed(controller, resolved)
        trip_lead_s = time.perf_counter() - prime_started
        play_channels = {
            key: value
            for key, value in resolved.items()
            if key != "backup_trip_coil"
        }
        _raise_if_stopped(should_stop)
        t0 = voltage.start()
        trip_thread = threading.Thread(
            target=_trip_at_fault,
            args=(
                controller, play_channels, t0, trip_lead_s,
                stop_event, should_stop, trip_box,
            ),
            name="fault-current-trip",
            daemon=True,
        )
        trip_thread.start()
        _wait_until(t0 + PRE_S, should_stop, stop_event)
        current.write(_milliamp_levels(current_channels, TC1_ON_MA))
        _wait_until(t0 + PRE_S + TC1_PULSE_S, should_stop, stop_event)
        current.write(_milliamp_levels(current_channels, 0.0))
        try:
            baseline = _live_ts(fetch(board))
        except LiveError:
            baseline = None
        measured, samples = _peak_after_fault(
            fetch, board, baseline, should_stop, stop_event,
        )
        if trip_thread is not None:
            trip_thread.join(timeout=TRIP_PRE_TRIGGER_S + 2.0)
        _raise_if_stopped(should_stop)
        if trip_box.get("stopped"):
            raise MvbcmStopped("stopped by user")
        try:
            voltage.wait(timeout=TAIL_S + 2.0)
        except Exception:
            pass
        row = _score_row(
            measured, expect, low, high, samples, trip_box.get("error"),
        )
        if on_row is not None:
            on_row(row)
        return [row]
    except MvbcmStopped:
        raise
    except Exception as exc:
        row = _fail_row(expect, low, high, str(exc))
        if on_row is not None:
            on_row(row)
        return [row]
    finally:
        if trip_thread is not None and trip_thread.is_alive():
            stop_event.set()
            trip_thread.join(timeout=3.0)
        if current is not None:
            try:
                current.close()
            except Exception:
                pass
        if voltage is not None:
            try:
                voltage.close()
            except Exception:
                pass


def _open_trip(channels, module, controller_factory):
    _ensure_daq_path()
    resolved = channels or default_trip_channels()
    if module is None:
        module = find_digital_module(device_name_from_channels(resolved))
    resolved = resolve_channels(module, resolved)
    factory = controller_factory or open_breaker_controller
    return resolved, factory(module)


def _prime_breaker_closed(controller, channels: dict[str, str]) -> None:
    """Breaker closed: 52A high. 52B, close, trip coil, and backup stay low."""
    for key, state in _CLOSED_DIGITAL.items():
        channel = channels.get(key)
        if not channel:
            continue
        if controller.set_output_line(channel, state):
            continue
        detail = ""
        getter = getattr(controller, "task_manager", None)
        if getter is not None and hasattr(getter, "get_last_error"):
            detail = (getter.get_last_error() or "").strip()
        raise RuntimeError(detail or f"Could not set {key} before the fault")


def _trip_at_fault(
    controller, channels, t0, trip_lead_s, stop_event, should_stop, trip_box,
) -> None:
    """Start the wet trip so the coil rises at the fault, not 200 ms later."""
    try:
        _wait_until(
            t0 + PRE_S - TRIP_PRE_TRIGGER_S - max(0.0, trip_lead_s),
            should_stop,
            stop_event,
        )
        _drive_trip(controller, channels, stop_event)
    except MvbcmStopped:
        trip_box["stopped"] = True
    except Exception as exc:
        trip_box["error"] = exc


def _drive_trip(controller, channels, stop_event) -> None:
    loop = getattr(controller, "_op_loop_thread", None)
    if loop is not None and loop.is_alive():
        raise RuntimeError("Stop the loop before applying a single trip or close")
    lock = controller._waveform_lock
    if not lock.acquire(blocking=False):
        raise RuntimeError("Trip/close already running")
    try:
        controller.play_breaker_operation(channels, "trip", stop_event)
    finally:
        lock.release()


def _wait_until(deadline: float, should_stop, stop_event: threading.Event) -> None:
    while True:
        if stop_event.is_set() or (should_stop is not None and should_stop()):
            stop_event.set()
            raise MvbcmStopped("stopped by user")
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return
        if remaining > 0.02:
            time.sleep(min(0.01, remaining * 0.5))


def _live_ts(payload: dict):
    if not isinstance(payload, dict):
        return None
    return payload.get("ts_ms")


def _peak_after_fault(fetch, board, baseline_ts, should_stop, stop_event) -> tuple[float, int]:
    """Max iph peak_eng on live samples published after the fault."""
    deadline = time.monotonic() + SCORE_WINDOW_S
    best = None
    seen = 0
    last_error: Exception | None = None
    skip = baseline_ts
    while time.monotonic() < deadline:
        _raise_if_stopped(should_stop)
        if stop_event.is_set():
            raise MvbcmStopped("stopped by user")
        try:
            payload = fetch(board)
            ts = _live_ts(payload)
            if skip is not None and ts == skip:
                time.sleep(_LIVE_POLL_S)
                continue
            value = measure(payload, "iph", "peak_eng")
        except LiveError as exc:
            last_error = exc
            time.sleep(_LIVE_POLL_S)
            continue
        best = value if best is None else max(best, value)
        seen += 1
        skip = ts
        time.sleep(_LIVE_POLL_S)
    if best is None:
        if last_error is not None:
            raise last_error
        raise LiveError("live sample did not update after the fault")
    return best, seen


def _score_row(measured, expect, low, high, samples, trip_error) -> dict:
    ok = in_limits(measured, low, high) and trip_error is None
    note = (
        f"iph 30% for {PRE_S:g}s then 90% quarter-cycle "
        f"({card_peak_volts(FAULT_FRACTION):.3f} V peak on the 9264); "
        f"tc2 cc imtr held at 0 mA; "
        f"tc1 {TC1_ON_MA:g} mA for {TC1_PULSE_S * 1000:g} ms; "
        f"52A high then falls, 52B low then rises, close low, backup low; "
        f"{samples} live sample(s)"
    )
    if trip_error is not None:
        note = f"trip failed: {trip_error}; {note}"
    elif not in_limits(measured, low, high):
        note = f"expected [{low:.3f}, {high:.3f}] A peak, got {measured}; {note}"
    return {
        "group": "fault_current",
        "test_id": "FAULT-CURRENT",
        "step": "1",
        "signal": "iph",
        "phase": "peak",
        "stimulus_value": f"{expect:.3f}",
        "unit": "A PEAK",
        "measured": f"{measured:.3f}",
        "field": "peak_eng",
        "min": f"{low:.3f}",
        "max": f"{high:.3f}",
        "result": "PASS" if ok else "FAIL",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "note": note,
    }


def _fail_row(expect, low, high, note: str) -> dict:
    return {
        "group": "fault_current",
        "test_id": "FAULT-CURRENT",
        "step": "1",
        "signal": "iph",
        "phase": "peak",
        "stimulus_value": f"{expect:.3f}",
        "unit": "A PEAK",
        "measured": "",
        "field": "peak_eng",
        "min": f"{low:.3f}",
        "max": f"{high:.3f}",
        "result": "FAIL",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "note": note,
    }
