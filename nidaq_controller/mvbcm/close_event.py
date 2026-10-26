"""One-shot close event, the mirror of the fault-current trip.

For 2 seconds the breaker is open: iph on NI-9264 ao0 is held at 0, every
9266 current is 0 mA, 52B is high, and 52A, the trip coil, the close coil,
and backup are low.

At 2.000 s, cc is 20 mA for 5 ms and the wet close sequence runs. The close
coil is high for 10 ms, 52B falls on that edge, and 52A rises 100 ms later.
Trip coil and backup stay low. iph stays 0 through the coil pulse. The 30%
sine starts when 52A rises and runs for 2 seconds.

``play_breaker_operation`` holds the open state for its pre-trigger before
the coil rises, so that call starts one pre-trigger before the command.
The coil itself rises at 2.000 s.

This module is not called from the main MV BCM run. The Close event
button is the caller until the sequence is confirmed on the bench.
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
from nidaq_controller.mvbcm.live import LiveError, fetch_live, in_limits, measure
from nidaq_controller.mvbcm.stimuli.current import CurrentStimulus, find_9266
from nidaq_controller.mvbcm.stimuli.voltage import VoltageStimulus, find_9264

# Nominal iph full scale: 20 A RMS = 333 mV RMS at the board. The fixture
# divides the 9264 by 11. 30% is 6.0 A RMS. The sheet window is ±0.2 A.
IPH_FS_MV = 333.0
IPH_FS_A = 20.0
IPH_FRACTION = 0.30
IPH_TOL_A = 0.2
OPEN_S = 2.0
SINE_S = 2.0
FREQUENCY_HZ = 50.0
CC_ON_MA = 20.0
CC_PULSE_S = 0.005
SCORE_WINDOW_S = 4.0
_LIVE_POLL_S = 0.5
# First live window after the sine starts still contains the zero hold.
_SINE_SETTLE_S = 1.0

# Breaker open, before the close command.
_OPEN_DIGITAL = {
    "trip_coil": False,
    "backup_trip_coil": False,
    "switch_52a": False,
    "switch_52b": True,
    "close_coil": False,
}


def _current_channels() -> list[MappedChannel]:
    """Every 9266 current in the map: tc1, tc2, cc, imtr."""
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
    """All currents off, except cc when the close command is on."""
    levels = {channel.signal: 0.0 for channel in channels}
    levels["cc"] = cc_ma
    return levels


def expected_rms_a() -> float:
    """Engineering amps RMS of the 30% sine."""
    return IPH_FRACTION * IPH_FS_A


def _close_done_s() -> float:
    """Time from the open-state start until 52A rises."""
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
    """Hold the breaker open, close it, then score the 30% iph sine."""
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
        t0 = time.perf_counter()
        close_thread = threading.Thread(
            target=_close_at_command,
            args=(
                controller, resolved, t0, close_lead_s,
                stop_event, should_stop, close_box,
            ),
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
        measured, samples = _rms_after_sine(
            fetch, board, baseline, sine_at, should_stop, stop_event,
        )
        _wait_until(sine_at + SINE_S, should_stop, stop_event)
        if close_thread is not None:
            close_thread.join(timeout=TRIP_PRE_TRIGGER_S + 2.0)
        _raise_if_stopped(should_stop)
        if close_box.get("stopped"):
            raise MvbcmStopped("stopped by user")
        row = _score_row(
            measured, expect, low, high, samples, millivolts, close_box.get("error"),
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


def _open_digital(channels, module, controller_factory):
    _ensure_daq_path()
    resolved = channels or default_trip_channels()
    if module is None:
        module = find_digital_module(device_name_from_channels(resolved))
    resolved = resolve_channels(module, resolved)
    factory = controller_factory or open_breaker_controller
    return resolved, factory(module)


def _prime_breaker_open(controller, channels: dict[str, str]) -> None:
    """Breaker open: 52B high. 52A, close, trip coil, and backup stay low."""
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


def _close_at_command(
    controller, channels, t0, close_lead_s, stop_event, should_stop, close_box,
) -> None:
    """Start the wet close so the coil rises at 2.000 s, not 200 ms later."""
    try:
        _wait_until(
            t0 + OPEN_S - TRIP_PRE_TRIGGER_S - max(0.0, close_lead_s),
            should_stop,
            stop_event,
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


def _rms_after_sine(
    fetch, board, baseline_ts, sine_at, should_stop, stop_event,
) -> tuple[float, int]:
    """Latest iph rms_eng published after the 30% sine has been running."""
    deadline = sine_at + max(SINE_S, SCORE_WINDOW_S)
    latest = None
    settled = None
    seen = 0
    last_error: Exception | None = None
    skip = baseline_ts
    while time.perf_counter() < deadline:
        _raise_if_stopped(should_stop)
        if stop_event.is_set():
            raise MvbcmStopped("stopped by user")
        try:
            payload = fetch(board)
            ts = _live_ts(payload)
            if skip is not None and ts == skip:
                time.sleep(_LIVE_POLL_S)
                continue
            value = measure(payload, "iph", "rms_eng")
        except LiveError as exc:
            last_error = exc
            time.sleep(_LIVE_POLL_S)
            continue
        latest = value
        seen += 1
        skip = ts
        if time.perf_counter() >= sine_at + _SINE_SETTLE_S:
            settled = value
        if settled is not None and time.perf_counter() >= sine_at + SINE_S:
            break
        time.sleep(_LIVE_POLL_S)
    chosen = settled if settled is not None else latest
    if chosen is None:
        if last_error is not None:
            raise last_error
        raise LiveError("live sample did not update after the close")
    return chosen, seen


def _score_row(measured, expect, low, high, samples, millivolts, close_error) -> dict:
    ok = in_limits(measured, low, high) and close_error is None
    note = (
        f"breaker open {OPEN_S:g}s with iph at 0; "
        f"cc {CC_ON_MA:g} mA for {CC_PULSE_S * 1000:g} ms; "
        f"close coil 10 ms, 52B falls, 52A rises; "
        f"trip and backup stay low; "
        f"iph {millivolts:g} mV RMS at {FREQUENCY_HZ:g} Hz for {SINE_S:g}s "
        f"after 52A rises; {samples} live sample(s)"
    )
    if close_error is not None:
        note = f"close failed: {close_error}; {note}"
    elif not in_limits(measured, low, high):
        note = f"expected [{low:.3f}, {high:.3f}] A RMS, got {measured}; {note}"
    return _row(measured, expect, low, high, "PASS" if ok else "FAIL", note)


def _fail_row(expect, low, high, note: str) -> dict:
    return _row(None, expect, low, high, "FAIL", note)


def _row(measured, expect, low, high, result: str, note: str) -> dict:
    return {
        "group": "close_event",
        "test_id": "CLOSE-EVENT",
        "step": "1",
        "signal": "iph",
        "phase": "rms",
        "stimulus_value": f"{expect:.3f}",
        "unit": "A RMS",
        "measured": "" if measured is None else f"{measured:.3f}",
        "field": "rms_eng",
        "min": f"{low:.3f}",
        "max": f"{high:.3f}",
        "result": result,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "note": note,
    }
