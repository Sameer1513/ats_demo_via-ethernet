"""MTR-05 motor run time.

imtr on NI-9266 ao4 is held at 12 mA (50 A on the 4–20 mA scale) for
1, 3, 5, and 10 s, then returned to 0 mA. The score is the board's
completed-run timer, ``b7.motor_run_last_ms``, against ±0.1% of that
interval. This module is not called from the main Run path.
"""

from __future__ import annotations

import time
from datetime import datetime

from nidaq_controller.mvbcm.breaker_ops import MvbcmStopped, _raise_if_stopped
from nidaq_controller.mvbcm.channel_map import GROUPS, MappedChannel
from nidaq_controller.mvbcm.live import LiveError, b7_value, fetch_live, in_limits
from nidaq_controller.mvbcm.stimuli.current import CurrentStimulus, find_9266

MOTOR_MA = 12.0
RUNS_S = (1.0, 3.0, 5.0, 10.0)
TOL_FRACTION = 0.001
POLL_S = 0.5
SETTLE_S = 15.0


def run_motor_run(
    board: str,
    *,
    voltage_device: str | None = None,
    current_device: str | None = None,
    channels=None,
    module=None,
    controller_factory=None,
    fetch=None,
    on_row=None,
    should_stop=None,
    timeout: float = 5.0,
) -> list[dict]:
    """Hold motor current for each MTR-05 interval and score the last run."""
    del voltage_device, channels, module, controller_factory
    if fetch is None:
        def fetch(url, _timeout=timeout):
            return fetch_live(url, timeout=_timeout)

    cdev = current_device or find_9266()
    current: CurrentStimulus | None = None
    rows: list[dict] = []
    try:
        current_channels = _current_channels()
        current = CurrentStimulus(cdev, current_channels)
        current.write(_levels(current_channels, 0.0))
        for step, seconds in enumerate(RUNS_S, start=1):
            _raise_if_stopped(should_stop)
            row = _run_one(
                board, current, current_channels, seconds, step, fetch, should_stop,
            )
            rows.append(row)
            if on_row is not None:
                on_row(row)
        return rows
    except MvbcmStopped:
        raise
    except Exception as exc:
        row = _result("1", "", "", "", "", "FAIL", str(exc))
        rows.append(row)
        if on_row is not None:
            on_row(row)
        return rows
    finally:
        if current is not None:
            try:
                current.close()
            except Exception:
                pass


def _run_one(board, current, current_channels, seconds, step, fetch, should_stop) -> dict:
    expect_ms = int(round(seconds * 1000.0))
    tol_ms = expect_ms * TOL_FRACTION
    low_s = (expect_ms - tol_ms) / 1000.0
    high_s = (expect_ms + tol_ms) / 1000.0
    stimulus = f"{seconds:.3f}"
    low_txt = f"{low_s:.3f}"
    high_txt = f"{high_s:.3f}"
    try:
        before = fetch(board)
        before_count = int(b7_value(before, "motor_run_count"))
    except (LiveError, TypeError, ValueError) as exc:
        return _result(str(step), stimulus, "", low_txt, high_txt, "FAIL", str(exc))

    current.write(_levels(current_channels, MOTOR_MA))
    started = time.perf_counter()
    try:
        _wait_until(started + seconds, should_stop)
        current.write(_levels(current_channels, 0.0))
    except MvbcmStopped:
        try:
            current.write(_levels(current_channels, 0.0))
        except Exception:
            pass
        raise

    payload = _poll_completed(
        fetch, board, before_count, should_stop, time.perf_counter() + SETTLE_S,
    )
    note = f"imtr {MOTOR_MA:.0f} mA for {stimulus} s"
    if payload is None:
        return _result(str(step), stimulus, "", low_txt, high_txt, "FAIL", note + "; no live payload")
    try:
        last_ms = float(b7_value(payload, "motor_run_last_ms"))
        count = int(b7_value(payload, "motor_run_count"))
    except (LiveError, TypeError, ValueError) as exc:
        return _result(str(step), stimulus, "", low_txt, high_txt, "FAIL", f"{note}; {exc}")
    measured_s = last_ms / 1000.0
    counted = count >= before_count + 1
    ok = counted and in_limits(last_ms, expect_ms - tol_ms, expect_ms + tol_ms)
    detail = f"{note}; b7.motor_run_last_ms={last_ms:.3f}; motor_run_count {before_count} -> {count}"
    if not counted:
        detail = f"motor_run_count did not increase; {detail}"
    elif not ok:
        detail = f"expected [{low_txt}, {high_txt}] s, got {measured_s:.3f}; {detail}"
    return _result(
        str(step), stimulus, f"{measured_s:.3f}", low_txt, high_txt,
        "PASS" if ok else "FAIL", detail,
    )


def _poll_completed(fetch, board, before_count, should_stop, deadline):
    """Wait until the run has finished and the counter has moved."""
    last = None
    while True:
        _raise_if_stopped(should_stop)
        try:
            payload = fetch(board)
        except LiveError:
            payload = None
        if payload is not None:
            last = payload
            try:
                running = _is_running(b7_value(payload, "motor_running"))
                count = int(b7_value(payload, "motor_run_count"))
            except (LiveError, TypeError, ValueError):
                running = True
                count = before_count
            if count >= before_count + 1 and not running:
                return payload
        if time.perf_counter() >= deadline:
            return last
        time.sleep(POLL_S)


def _is_running(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


def _current_channels() -> list[MappedChannel]:
    for group in GROUPS:
        if group.id == "tc_current":
            return list(group.channels)
    raise RuntimeError("tc_current group is not configured")


def _levels(channels: list[MappedChannel], imtr_ma: float) -> dict[str, float]:
    levels = {channel.signal: 0.0 for channel in channels}
    levels["imtr"] = imtr_ma
    return levels


def _wait_until(deadline: float, should_stop) -> None:
    while True:
        _raise_if_stopped(should_stop)
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return
        if remaining > 0.004:
            time.sleep(min(0.002, remaining * 0.5))


def _result(step, stimulus, measured, low, high, result, note) -> dict:
    return {
        "group": "motor_run",
        "test_id": "MTR-05",
        "step": step,
        "signal": "imtr",
        "phase": "motor_run_last_ms",
        "stimulus_value": stimulus,
        "unit": "s",
        "measured": measured,
        "field": "motor_run_last_ms",
        "min": low,
        "max": high,
        "result": result,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "note": note,
    }
