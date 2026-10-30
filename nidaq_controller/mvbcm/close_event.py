"""Close event: breaker starts open, then the close command, then iph at 30%.

For 2 s iph is held at 0, the four currents are 0 mA, 52B is high, and
52A, both coils, and backup are low. At 2.000 s cc is 20 mA for 5 ms and
the wet close runs. The 30% sine starts when 52A rises.

The 6.0 A check uses ``rms_eng`` and ``b7.iph_rms_primary`` after a full
new live window. The close timing comes from ``operations.recent``.
``iph_rms`` on that row is not the pass/fail number.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any

from nidaq_controller.constants import (
    TRIP_OPENING_TRAVEL_S,
    TRIP_PRE_TRIGGER_S,
    TRIP_PULSE_S,
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
from nidaq_controller.mvbcm.stimuli.voltage import VoltageStimulus, find_9264

IPH_FS_MV = 333.0
IPH_FS_A = 20.0
IPH_FRACTION = 0.30
IPH_TOL_A = 0.2
OPEN_S = 2.0
SINE_S = 2.0
FREQUENCY_HZ = 50.0
CC_ON_MA = 20.0
CC_PULSE_S = 0.005
EVENT_WAIT_S = 30.0
_LIVE_POLL_S = 0.5
_PUBLISH_S = 1.0
CLOSE_TIME_MS = 10.0
CLOSE_TRAVEL_MS = 100.0
CLOSE_TOTAL_MS = 110.0
CLOSE_TIME_TOL_MS = 5.0
CLOSE_TRAVEL_TOL_MS = 15.0

_OPEN_DIGITAL = {
    "trip_coil": False,
    "backup_trip_coil": False,
    "switch_52a": False,
    "switch_52b": True,
    "close_coil": False,
}
_CLOSED_ACTIVE = {"52a": True, "52b": False, "trip": False, "close": False}


def _current_channels() -> list[MappedChannel]:
    for group in GROUPS:
        if group.id == "tc_current":
            return list(group.channels)
    raise RuntimeError("tc_current group is not configured")


def _iph_channel() -> MappedChannel:
    for group in GROUPS:
        if group.id == "iph_voltage" and group.channels:
            return group.channels[0]
    raise RuntimeError("iph_voltage group is not configured")


def _milliamp_levels(channels: list[MappedChannel], cc_ma: float) -> dict[str, float]:
    levels = {channel.signal: 0.0 for channel in channels}
    levels["cc"] = cc_ma
    return levels


def expected_rms_a() -> float:
    return IPH_FRACTION * IPH_FS_A


def _close_done_s() -> float:
    return OPEN_S + TRIP_PULSE_S + TRIP_OPENING_TRAVEL_S


def run_close_event(
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
    """Hold the breaker open, close it, then score the live tags."""
    if fetch is None:
        def fetch(url, _timeout=timeout):
            return fetch_live(url, timeout=_timeout)

    expect = expected_rms_a()
    low = expect - IPH_TOL_A
    high = expect + IPH_TOL_A
    vdev = voltage_device or find_9264()
    cdev = current_device or find_9266()
    resolved, controller = _open_digital(channels, module, controller_factory)
    iph = _iph_channel()
    millivolts = IPH_FRACTION * IPH_FS_MV

    voltage: VoltageStimulus | None = None
    current: CurrentStimulus | None = None
    close_thread: threading.Thread | None = None
    stop_event = threading.Event()
    close_box: dict[str, Any] = {}
    before_ts: int | None = None
    try:
        current_channels = _current_channels()
        voltage = VoltageStimulus(vdev, [iph])
        current = CurrentStimulus(cdev, current_channels)
        current.write(_milliamp_levels(current_channels, 0.0))
        voltage.write({iph.signal: 0.0}, frequency=FREQUENCY_HZ)
        prime_started = time.perf_counter()
        _prime_breaker_open(controller, resolved)
        close_lead_s = time.perf_counter() - prime_started
        _raise_if_stopped(should_stop)
        try:
            before_ts = max_recent_ts(fetch(board))
        except LiveError:
            before_ts = None
        t0 = time.perf_counter()
        close_thread = threading.Thread(
            target=_close_at_command,
            args=(controller, resolved, t0, close_lead_s, stop_event, should_stop, close_box),
            name="close-event",
            daemon=True,
        )
        close_thread.start()
        _wait_until(t0 + OPEN_S, should_stop, stop_event)
        current.write(_milliamp_levels(current_channels, CC_ON_MA))
        _wait_until(t0 + OPEN_S + CC_PULSE_S, should_stop, stop_event)
        current.write(_milliamp_levels(current_channels, 0.0))
        _wait_until(t0 + _close_done_s(), should_stop, stop_event)
        try:
            baseline = _live_ts(fetch(board))
        except LiveError:
            baseline = None
        voltage.write({iph.signal: millivolts}, frequency=FREQUENCY_HZ)
        sine_at = time.perf_counter()
        rms_payload, measured = _rms_after_publish(
            fetch, board, baseline, sine_at, should_stop, stop_event,
        )
        _wait_until(sine_at + SINE_S, should_stop, stop_event)
        if close_thread is not None:
            close_thread.join(timeout=TRIP_PRE_TRIGGER_S + 2.0)
        _raise_if_stopped(should_stop)
        if close_box.get("stopped"):
            raise MvbcmStopped("stopped by user")
        rows = _score_close(
            fetch, board, before_ts, rms_payload, measured, expect, low, high,
            millivolts, close_box.get("error"), should_stop,
        )
        if on_row is not None:
            for row in rows:
                on_row(row)
        return rows
    except MvbcmStopped:
        raise
    except Exception as exc:
        row = _result("iph", "rms", f"{expect:.3f}", "A RMS", "", f"{low:.3f}", f"{high:.3f}", "FAIL", str(exc))
        if on_row is not None:
            on_row(row)
        return [row]
    finally:
        if close_thread is not None and close_thread.is_alive():
            stop_event.set()
            close_thread.join(timeout=3.0)
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


def _rms_after_publish(fetch, board, baseline_ts, sine_at, should_stop, stop_event):
    """iph rms_eng from a ts_ms at least one publish period after the sine starts.

    A file that updates a few hundred milliseconds after the sine still
    describes the hold at 0. Its ts_ms is skipped.
    """
    deadline = sine_at + SINE_S + EVENT_WAIT_S
    last_error = None
    while time.perf_counter() < deadline:
        _raise_if_stopped(should_stop)
        if stop_event.is_set():
            raise MvbcmStopped("stopped by user")
        if time.perf_counter() < sine_at + _PUBLISH_S:
            time.sleep(_LIVE_POLL_S)
            continue
        try:
            payload = fetch(board)
            ts = _live_ts(payload)
            if not _ts_after_publish(baseline_ts, ts):
                time.sleep(_LIVE_POLL_S)
                continue
            value = measure(payload, "iph", "rms_eng")
        except LiveError as exc:
            last_error = exc
            time.sleep(_LIVE_POLL_S)
            continue
        return payload, value
    if last_error is not None:
        raise last_error
    raise LiveError("live sample did not update after the 30% sine")


def _score_close(
    fetch, board, before_ts, rms_payload, measured, expect, low, high,
    millivolts, drive_error, should_stop,
) -> list[dict]:
    event_payload = None
    event = None
    wait_error = None
    try:
        event_payload, event = wait_for_recent_row(
            fetch, board, before_ts, ("close",),
            timeout_s=EVENT_WAIT_S, should_stop=should_stop, stop_error=MvbcmStopped,
        )
    except MvbcmStopped:
        raise
    except LiveError as exc:
        wait_error = exc
    state_payload = event_payload or rms_payload
    rows = [
        _rms_row(measured, expect, low, high, millivolts, drive_error),
        _primary_row(rms_payload, measured, expect, low, high),
        *_timing_rows(event, wait_error),
        _state_row(state_payload, "closed", wait_error),
        _digital_row(state_payload, wait_error),
        _aux_row(state_payload, wait_error),
        _zero_row(event, "arcing_time_ms", "ms", wait_error),
        _zero_row(event, "i2t_increment", "A2s", wait_error),
    ]
    return rows


def _rms_row(measured, expect, low, high, millivolts, drive_error) -> dict:
    ok = measured is not None and in_limits(measured, low, high) and drive_error is None
    note = f"iph {millivolts:g} mV RMS at {FREQUENCY_HZ:g} Hz after 52A rises"
    shown = "" if measured is None else f"{measured:.3f}"
    if drive_error is not None:
        note = f"close failed: {drive_error}; {note}"
    elif measured is None or not in_limits(measured, low, high):
        note = f"expected [{low:.3f}, {high:.3f}] A RMS, got {shown or 'none'}; {note}"
    return _result("iph", "rms", f"{expect:.3f}", "A RMS", shown, f"{low:.3f}", f"{high:.3f}", "PASS" if ok else "FAIL", note)


def _primary_row(payload, rms, expect, low, high) -> dict:
    if payload is None or rms is None:
        return _result("iph", "iph_rms_primary", f"{expect:.3f}", "A", "", f"{low:.3f}", f"{high:.3f}", "FAIL", "no live sample after the sine")
    try:
        primary = float(b7_value(payload, "iph_rms_primary"))
        unit = str(b7_value(payload, "iph_rms_primary_unit") or "")
    except (LiveError, TypeError, ValueError) as exc:
        return _result("iph", "iph_rms_primary", f"{expect:.3f}", "A", "", f"{low:.3f}", f"{high:.3f}", "FAIL", str(exc))
    ok = in_limits(primary, low, high) and abs(primary - rms) <= IPH_TOL_A and unit.strip().upper() == "A"
    note = f"b7.iph_rms_primary unit={unit or 'missing'}; rms_eng={rms:.3f}"
    if not ok:
        note = f"expected [{low:.3f}, {high:.3f}] A matching rms_eng, got {primary:.3f} {unit}; {note}"
    return _result(
        "iph", "iph_rms_primary", f"{expect:.3f}", "A", f"{primary:.3f}",
        f"{low:.3f}", f"{high:.3f}", "PASS" if ok else "FAIL", note,
    )


def _timing_rows(event, wait_error) -> list[dict]:
    specs = (
        ("close_time_ms", CLOSE_TIME_MS, CLOSE_TIME_TOL_MS),
        ("close_travel_ms", CLOSE_TRAVEL_MS, CLOSE_TRAVEL_TOL_MS),
        ("total_close_ms", CLOSE_TOTAL_MS, CLOSE_TRAVEL_TOL_MS),
    )
    rows = []
    for key, expect, tol in specs:
        low, high = expect - tol, expect + tol
        if event is None:
            rows.append(_result("close", key, f"{expect:.0f}", "ms", "", f"{low:.0f}", f"{high:.0f}", "FAIL", str(wait_error or "no close row")))
            continue
        try:
            measured = recent_number(event, key)
        except LiveError as exc:
            rows.append(_result("close", key, f"{expect:.0f}", "ms", "", f"{low:.0f}", f"{high:.0f}", "FAIL", str(exc)))
            continue
        ok = in_limits(measured, low, high)
        note = f"operations.recent.{key}"
        if not ok:
            note = f"expected [{low:.0f}, {high:.0f}] ms, got {measured:.3f}"
        rows.append(_result(
            "close", key, f"{expect:.0f}", "ms", f"{measured:.3f}",
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
    note = "b7.breaker_state" if ok else f"expected {expect}, got {got or 'empty'}"
    return _result("breaker", "breaker_state", expect, "", got, expect, expect, "PASS" if ok else "FAIL", note)


def _digital_row(payload, wait_error) -> dict:
    if payload is None:
        return _result("digital", "active", "52a=1,52b=0", "", "", "", "", "FAIL", str(wait_error or "no live payload"))
    parts = []
    ok = True
    for signal, want in _CLOSED_ACTIVE.items():
        try:
            got = digital_active(payload, signal)
        except LiveError as exc:
            return _result("digital", "active", "52a=1,52b=0", "", "", "", "", "FAIL", str(exc))
        parts.append(f"{signal}={'1' if got else '0'}")
        if got != want:
            ok = False
    measured = ",".join(parts)
    note = "digital.active after the close" if ok else f"expected 52a=1,52b=0,trip=0,close=0, got {measured}"
    return _result("digital", "active", "52a=1,52b=0", "", measured, "", "", "PASS" if ok else "FAIL", note)


def _aux_row(payload, wait_error) -> dict:
    if payload is None:
        return _result("b7", "aux_input_failure", "0", "", "", "0", "0", "FAIL", str(wait_error or "no live payload"))
    try:
        got = int(b7_value(payload, "aux_input_failure"))
    except (LiveError, TypeError, ValueError) as exc:
        return _result("b7", "aux_input_failure", "0", "", "", "0", "0", "FAIL", str(exc))
    ok = got == 0
    note = "b7.aux_input_failure" if ok else f"52A and 52B both active ({got})"
    return _result("b7", "aux_input_failure", "0", "", str(got), "0", "0", "PASS" if ok else "FAIL", note)


def _zero_row(event, key: str, unit: str, wait_error) -> dict:
    if event is None:
        return _result("event", key, "0", unit, "", "0", "0", "FAIL", str(wait_error or "no close row"))
    try:
        measured = recent_number(event, key)
    except LiveError as exc:
        return _result("event", key, "0", unit, "", "0", "0", "FAIL", str(exc))
    ok = measured == 0.0
    note = f"operations.recent.{key}"
    if not ok:
        note = f"{key} is {measured}; a close does not drop 52A, so this stays 0"
    return _result("event", key, "0", unit, f"{measured:.3f}", "0", "0", "PASS" if ok else "FAIL", note)


def _result(signal, phase, stimulus, unit, measured, low, high, result, note) -> dict:
    return {
        "group": "close_event",
        "test_id": "CLOSE-EVENT",
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


def _open_digital(channels, module, controller_factory):
    _ensure_daq_path()
    resolved = channels or default_trip_channels()
    if module is None:
        module = find_digital_module(device_name_from_channels(resolved))
    resolved = resolve_channels(module, resolved)
    factory = controller_factory or open_breaker_controller
    return resolved, factory(module)


def _prime_breaker_open(controller, channels: dict[str, str]) -> None:
    for key, state in _OPEN_DIGITAL.items():
        channel = channels.get(key)
        if not channel:
            continue
        if controller.set_output_line(channel, state):
            continue
        detail = ""
        getter = getattr(controller, "task_manager", None)
        if getter is not None and hasattr(getter, "get_last_error"):
            detail = (getter.get_last_error() or "").strip()
        raise RuntimeError(detail or f"Could not set {key} before the close")


def _close_at_command(controller, channels, t0, close_lead_s, stop_event, should_stop, close_box) -> None:
    try:
        _wait_until(
            t0 + OPEN_S - TRIP_PRE_TRIGGER_S - max(0.0, close_lead_s),
            should_stop, stop_event,
        )
        _drive_close(controller, channels, stop_event)
    except MvbcmStopped:
        close_box["stopped"] = True
    except Exception as exc:
        close_box["error"] = exc


def _drive_close(controller, channels, stop_event) -> None:
    loop = getattr(controller, "_op_loop_thread", None)
    if loop is not None and loop.is_alive():
        raise RuntimeError("Stop the loop before applying a single trip or close")
    lock = controller._waveform_lock
    if not lock.acquire(blocking=False):
        raise RuntimeError("Trip/close already running")
    try:
        controller.play_breaker_operation(channels, "close", stop_event)
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


def _ts_after_publish(baseline_ts, ts) -> bool:
    """True when ``ts`` is a later export, at least one publish period on."""
    if ts is None:
        return False
    if baseline_ts is None:
        return True
    try:
        return int(ts) >= int(baseline_ts) + int(_PUBLISH_S * 1000)
    except (TypeError, ValueError):
        return ts != baseline_ts
