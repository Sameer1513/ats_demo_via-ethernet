"""One-shot iph fault current plus the wet trip sequence.

iph on NI-9264 ao0 is one finite buffer: 2 s of 50 Hz sine at 30% of
nominal, then one 90% quarter-cycle joined at the zero crossing, then zero.
At that instant tc1 is 20 mA for 5 ms and the wet trip runs. Backup stays low.

Pass/fail uses the stored event, not the rolling window. ``iph_peak`` on the
new ``operations.recent`` row is the fault peak. ``peak_eng`` is only noted.
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
from nidaq_controller.mvbcm.live import (
    LiveError,
    b7_value,
    digital_active,
    fetch_live,
    in_limits,
    max_recent_ts,
    measure,
    recent_number,
    wait_for_recent_row,
)
from nidaq_controller.mvbcm.stimuli.current import CurrentStimulus, find_9266
from nidaq_controller.mvbcm.stimuli.voltage import find_9264

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
PEAK_TOL_A = 1.0
TC1_ON_MA = 20.0
TC1_PULSE_S = 0.005
EVENT_WAIT_S = 30.0
TRIP_TIME_MS = 10.0
TRIP_TRAVEL_MS = 100.0
TRIP_TOTAL_MS = 110.0
TRIP_TIME_TOL_MS = 5.0
TRIP_TRAVEL_TOL_MS = 15.0

_IPH = "ao0"
_CLOSED_DIGITAL = {
    "trip_coil": False,
    "backup_trip_coil": False,
    "switch_52a": True,
    "switch_52b": False,
    "close_coil": False,
}
_OPEN_ACTIVE = {"52a": False, "52b": True, "trip": False, "close": False}


def card_peak_volts(fraction: float) -> float:
    rms_volts = (fraction * IPH_FS_MV / 1000.0) * IPH_SCALE
    return rms_volts * math.sqrt(2.0)


def expected_fault_peak_a() -> float:
    return FAULT_FRACTION * IPH_FS_A * math.sqrt(2.0)


def _current_channels() -> list[MappedChannel]:
    for group in GROUPS:
        if group.id == "tc_current":
            return list(group.channels)
    raise RuntimeError("tc_current group is not configured")


def _milliamp_levels(channels: list[MappedChannel], tc1_ma: float) -> dict[str, float]:
    levels = {channel.signal: 0.0 for channel in channels}
    levels["tc1"] = tc1_ma
    return levels


def build_iph_fault_wave() -> np.ndarray:
    """30% sine, then a 90% quarter-cycle at the zero crossing, then zeros."""
    samples_per_cycle = SAMPLE_RATE / FREQUENCY_HZ
    if samples_per_cycle != int(samples_per_cycle):
        raise RuntimeError("sample rate does not land 50 Hz on a sample")
    samples_per_cycle = int(samples_per_cycle)
    pre_n = int(round(PRE_S * SAMPLE_RATE))
    fault_n = int(round(FAULT_S * SAMPLE_RATE))
    tail_n = int(round(TAIL_S * SAMPLE_RATE))
    if pre_n % samples_per_cycle != 0 or fault_n != samples_per_cycle // 4:
        raise RuntimeError("fault record is not an integer cycle plus a quarter")
    peak_30 = card_peak_volts(PRE_FRACTION)
    peak_90 = card_peak_volts(FAULT_FRACTION)
    low, high = DEFAULT_VOLTAGE_RANGE
    if peak_90 > high or peak_30 > high:
        raise RuntimeError(f"iph fault peak {peak_90:.3f} V is outside the 9264 range")
    total = pre_n + fault_n + tail_n + 1
    index = np.arange(total, dtype=np.float64)
    sine = np.sin(2.0 * math.pi * FREQUENCY_HZ * index / SAMPLE_RATE)
    amplitude = np.empty(total, dtype=np.float64)
    amplitude[:pre_n] = peak_30
    amplitude[pre_n:pre_n + fault_n + 1] = peak_90
    amplitude[pre_n + fault_n + 1:] = 0.0
    wave = amplitude * sine
    if abs(float(wave[pre_n])) > 1e-6 or float(wave[pre_n + 1]) <= 0.0:
        raise RuntimeError("30% to 90% join is not a clean zero crossing")
    if abs(float(np.mean(wave[:pre_n]))) > 1e-3:
        raise RuntimeError("pre-fault sine has a DC offset")
    return wave


class _FiniteIph:
    def __init__(self, device_name: str, samples: np.ndarray):
        import nidaqmx

        physical = f"{device_name}/{_IPH}"
        low, high = DEFAULT_VOLTAGE_RANGE
        self._task = nidaqmx.Task()
        self._started = False
        try:
            self._task.ao_channels.add_ao_voltage_chan(physical, min_val=low, max_val=high)
            self._task.timing.cfg_samp_clk_timing(
                rate=SAMPLE_RATE,
                sample_mode=nidaqmx.constants.AcquisitionType.FINITE,
                samps_per_chan=int(len(samples)),
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
    """Play the fault record, trip, and score the stored event."""
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
    before_ts: int | None = None
    before_backup: int | None = None
    try:
        current_channels = _current_channels()
        voltage = _FiniteIph(vdev, wave)
        current = CurrentStimulus(cdev, current_channels)
        current.write(_milliamp_levels(current_channels, 0.0))
        prime_started = time.perf_counter()
        _prime_breaker_closed(controller, resolved)
        trip_lead_s = time.perf_counter() - prime_started
        play_channels = {
            key: value for key, value in resolved.items() if key != "backup_trip_coil"
        }
        _raise_if_stopped(should_stop)
        try:
            before = fetch(board)
            before_ts = max_recent_ts(before)
            before_backup = int(b7_value(before, "backup_trip_total"))
        except (LiveError, TypeError, ValueError):
            before_ts = None
            before_backup = None
        t0 = voltage.start()
        trip_thread = threading.Thread(
            target=_trip_at_fault,
            args=(controller, play_channels, t0, trip_lead_s, stop_event, should_stop, trip_box),
            name="fault-current-trip",
            daemon=True,
        )
        trip_thread.start()
        _wait_until(t0 + PRE_S, should_stop, stop_event)
        current.write(_milliamp_levels(current_channels, TC1_ON_MA))
        _wait_until(t0 + PRE_S + TC1_PULSE_S, should_stop, stop_event)
        current.write(_milliamp_levels(current_channels, 0.0))
        if trip_thread is not None:
            trip_thread.join(timeout=TRIP_PRE_TRIGGER_S + 2.0)
        _raise_if_stopped(should_stop)
        if trip_box.get("stopped"):
            raise MvbcmStopped("stopped by user")
        try:
            voltage.wait(timeout=TAIL_S + 2.0)
        except Exception:
            pass
        rows = _score_trip_event(
            fetch, board, before_ts, before_backup, expect, low, high,
            trip_box.get("error"), should_stop,
        )
        if on_row is not None:
            for row in rows:
                on_row(row)
        return rows
    except MvbcmStopped:
        raise
    except Exception as exc:
        row = _result("iph", "peak", f"{expect:.3f}", "A", "", f"{low:.3f}", f"{high:.3f}", "FAIL", str(exc))
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


def _score_trip_event(
    fetch, board, before_ts, before_backup, expect, low, high, drive_error, should_stop,
) -> list[dict]:
    payload = None
    event = None
    wait_error = None
    try:
        payload, event = wait_for_recent_row(
            fetch, board, before_ts, ("trip",),
            timeout_s=EVENT_WAIT_S, should_stop=should_stop, stop_error=MvbcmStopped,
            fallback_slots=("fault",),
        )
    except MvbcmStopped:
        raise
    except LiveError as exc:
        wait_error = exc
    if payload is None:
        try:
            payload = fetch(board)
        except LiveError as exc:
            wait_error = wait_error or exc
    rows = [
        _peak_row(payload, event, expect, low, high, drive_error, wait_error),
        *_timing_rows(event, wait_error),
        _state_row(payload, "open", wait_error),
        _digital_row(payload, _OPEN_ACTIVE, wait_error),
        _aux_row(payload, wait_error),
        _backup_row(payload, before_backup, wait_error),
        _positive_row(event, "arcing_time_ms", "ms", wait_error),
        _positive_row(event, "i2t_increment", "A2s", wait_error),
    ]
    return rows


def _peak_row(payload, event, expect, low, high, drive_error, wait_error) -> dict:
    window = ""
    if payload is not None:
        try:
            window = f"; window peak_eng={measure(payload, 'iph', 'peak_eng'):.3f}"
        except LiveError:
            window = ""
    if event is None:
        note = str(wait_error or "no event row")
        return _result("iph", "iph_peak", f"{expect:.3f}", "A", "", f"{low:.3f}", f"{high:.3f}", "FAIL", note + window)
    try:
        measured = recent_number(event, "iph_peak")
    except LiveError as exc:
        return _result("iph", "iph_peak", f"{expect:.3f}", "A", "", f"{low:.3f}", f"{high:.3f}", "FAIL", str(exc) + window)
    slot = str(event.get("rec_slot") or "")
    ok = in_limits(measured, low, high) and drive_error is None
    note = f"operations.recent rec_slot={slot} iph_peak{window}"
    if drive_error is not None:
        note = f"trip failed: {drive_error}; {note}"
    elif not in_limits(measured, low, high):
        note = f"expected [{low:.3f}, {high:.3f}] A, got {measured}; {note}"
    return _result(
        "iph", "iph_peak", f"{expect:.3f}", "A", f"{measured:.3f}",
        f"{low:.3f}", f"{high:.3f}", "PASS" if ok else "FAIL", note,
    )


def _timing_rows(event, wait_error) -> list[dict]:
    specs = (
        ("trip_time_ms", TRIP_TIME_MS, TRIP_TIME_TOL_MS),
        ("trip_travel_ms", TRIP_TRAVEL_MS, TRIP_TRAVEL_TOL_MS),
        ("total_trip1_ms", TRIP_TOTAL_MS, TRIP_TRAVEL_TOL_MS),
    )
    slot = str(event.get("rec_slot") or "").lower() if event else ""
    rows = []
    for key, expect, tol in specs:
        low, high = expect - tol, expect + tol
        if event is None or slot != "trip":
            why = str(wait_error) if event is None else f"rec_slot is {slot or 'missing'}; trip timing is on a trip row"
            rows.append(_result("trip", key, f"{expect:.0f}", "ms", "", f"{low:.0f}", f"{high:.0f}", "FAIL", why))
            continue
        try:
            measured = recent_number(event, key)
        except LiveError as exc:
            rows.append(_result("trip", key, f"{expect:.0f}", "ms", "", f"{low:.0f}", f"{high:.0f}", "FAIL", str(exc)))
            continue
        ok = in_limits(measured, low, high)
        note = f"operations.recent.{key}"
        if not ok:
            note = f"expected [{low:.0f}, {high:.0f}] ms, got {measured:.3f}"
        rows.append(_result(
            "trip", key, f"{expect:.0f}", "ms", f"{measured:.3f}",
            f"{low:.0f}", f"{high:.0f}", "PASS" if ok else "FAIL", note,
        ))
    return rows


def _state_row(payload, expect: str, wait_error) -> dict:
    if payload is None:
        return _result("breaker", "breaker_state", expect, "", "", expect, expect, "FAIL", str(wait_error or "no live payload"))
    try:
        got = str(b7_value(payload, "breaker_state") or "").strip().lower()
    except LiveError as exc:
        return _result("breaker", "breaker_state", expect, "", "", expect, expect, "FAIL", str(exc))
    ok = got == expect
    note = "b7.breaker_state"
    if not ok:
        note = f"expected {expect}, got {got or 'empty'}"
    return _result("breaker", "breaker_state", expect, "", got, expect, expect, "PASS" if ok else "FAIL", note)


def _digital_row(payload, expect: dict[str, bool], wait_error) -> dict:
    if payload is None:
        return _result("digital", "active", "open", "", "", "", "", "FAIL", str(wait_error or "no live payload"))
    parts = []
    ok = True
    for signal, want in expect.items():
        try:
            got = digital_active(payload, signal)
        except LiveError as exc:
            return _result("digital", "active", "open", "", "", "", "", "FAIL", str(exc))
        parts.append(f"{signal}={'1' if got else '0'}")
        if got != want:
            ok = False
    measured = ",".join(parts)
    note = "digital.active after the trip"
    if not ok:
        note = f"expected 52a=0,52b=1,trip=0,close=0, got {measured}"
    return _result("digital", "active", "52a=0,52b=1", "", measured, "", "", "PASS" if ok else "FAIL", note)


def _aux_row(payload, wait_error) -> dict:
    if payload is None:
        return _result("b7", "aux_input_failure", "0", "", "", "0", "0", "FAIL", str(wait_error or "no live payload"))
    try:
        got = int(b7_value(payload, "aux_input_failure"))
    except (LiveError, TypeError, ValueError) as exc:
        return _result("b7", "aux_input_failure", "0", "", "", "0", "0", "FAIL", str(exc))
    ok = got == 0
    note = "b7.aux_input_failure"
    if not ok:
        note = f"52A and 52B both active ({got})"
    return _result("b7", "aux_input_failure", "0", "", str(got), "0", "0", "PASS" if ok else "FAIL", note)


def _backup_row(payload, before_backup, wait_error) -> dict:
    if payload is None or before_backup is None:
        return _result("b7", "backup_trip_total", "", "", "", "", "", "FAIL", str(wait_error or "backup total was not read before the trip"))
    try:
        got = int(b7_value(payload, "backup_trip_total"))
    except (LiveError, TypeError, ValueError) as exc:
        return _result("b7", "backup_trip_total", str(before_backup), "", "", str(before_backup), str(before_backup), "FAIL", str(exc))
    ok = got == before_backup
    note = "backup held low"
    if not ok:
        note = f"backup_trip_total changed from {before_backup} to {got}"
    return _result(
        "b7", "backup_trip_total", str(before_backup), "", str(got),
        str(before_backup), str(before_backup), "PASS" if ok else "FAIL", note,
    )


def _positive_row(event, key: str, unit: str, wait_error) -> dict:
    if event is None:
        return _result("event", key, ">0", unit, "", "", "", "FAIL", str(wait_error or "no event row"))
    try:
        measured = recent_number(event, key)
    except LiveError as exc:
        return _result("event", key, ">0", unit, "", "", "", "FAIL", str(exc))
    ok = measured > 0.0
    note = f"operations.recent.{key}"
    if not ok:
        note = f"{key} is 0; 52A fall during the capture should store it"
    return _result("event", key, ">0", unit, f"{measured:.3f}", "", "", "PASS" if ok else "FAIL", note)


def _result(signal, phase, stimulus, unit, measured, low, high, result, note) -> dict:
    return {
        "group": "fault_current",
        "test_id": "FAULT-CURRENT",
        "step": "1",
        "signal": signal,
        "phase": phase,
        "stimulus_value": stimulus,
        "unit": unit,
        "measured": measured,
        "field": phase,
        "min": low,
        "max": high,
        "result": result,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "note": note,
    }


def _open_trip(channels, module, controller_factory):
    _ensure_daq_path()
    resolved = channels or default_trip_channels()
    if module is None:
        module = find_digital_module(device_name_from_channels(resolved))
    resolved = resolve_channels(module, resolved)
    factory = controller_factory or open_breaker_controller
    return resolved, factory(module)


def _prime_breaker_closed(controller, channels: dict[str, str]) -> None:
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


def _trip_at_fault(controller, channels, t0, trip_lead_s, stop_event, should_stop, trip_box) -> None:
    try:
        _wait_until(
            t0 + PRE_S - TRIP_PRE_TRIGGER_S - max(0.0, trip_lead_s),
            should_stop, stop_event,
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
