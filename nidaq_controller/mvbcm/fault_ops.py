"""Incomplete-travel fault trip/close vs board ``b7`` trip/close totals.

Fault trip: 10 ms trip (+ backup) pulse, 52A falls, **52B stays low**.
Wait ``FAULT_EVENT_WAIT_S`` (40 s), score ``trip_total`` +1, then reset
to open (52B high, 52A low).

Fault close: 10 ms close pulse, 52B falls, **52A stays low**.
Wait 40 s, score ``close_total`` +1, then reset to closed (52A high, 52B low).
"""

from __future__ import annotations

from nidaq_controller.constants import (
    TRIP_OPENING_TRAVEL_S,
    TRIP_POST_TRIGGER_S,
    TRIP_PRE_TRIGGER_S,
    TRIP_PULSE_S,
)
from nidaq_controller.mvbcm.breaker_ops import (
    MvbcmStopped,
    _raise_if_stopped,
    _result_row,
    _sleep_interruptible,
    b7_totals,
    default_trip_channels,
    device_name_from_channels,
    find_digital_module,
    open_breaker_controller,
    resolve_channels,
)
from nidaq_controller.mvbcm.live import LiveError, fetch_live

FAULT_EVENT_WAIT_S = 40.0
FAULT_RESET_SETTLE_S = 1.0


def run_fault_trip_close_test(
    board: str,
    *,
    device: str | None = None,
    channels: dict[str, str] | None = None,
    timeout: float = 30.0,
    cycles: int = 1,
    fetch=None,
    controller_factory=None,
    module=None,
    on_row=None,
    cleanup: bool = True,
    should_stop=None,
) -> list[dict]:
    """Fault trip then fault close ``cycles`` times; score trip/close +1."""
    if cycles < 1:
        raise ValueError("cycles must be >= 1")
    if fetch is None:
        def fetch(url, _timeout=timeout):
            return fetch_live(url, timeout=_timeout)

    mapping = channels or default_trip_channels()
    prefer = device or device_name_from_channels(mapping)
    if module is None:
        module = find_digital_module(prefer)
    resolved = resolve_channels(module, mapping)
    if controller_factory is None:
        controller_factory = open_breaker_controller
    controller = controller_factory(module)

    rows: list[dict] = []
    try:
        _reset_contacts(controller, resolved, closed=True, should_stop=should_stop)
        for cycle in range(1, cycles + 1):
            _raise_if_stopped(should_stop)
            row = _run_fault_op(
                board, fetch, controller, resolved, "trip",
                cycle=cycle, cycles=cycles,
                should_stop=should_stop,
            )
            rows.append(row)
            if on_row is not None:
                on_row(row)
            _reset_contacts(
                controller, resolved, closed=False, should_stop=should_stop,
            )
            _sleep_interruptible(FAULT_RESET_SETTLE_S, should_stop)

            _raise_if_stopped(should_stop)
            row = _run_fault_op(
                board, fetch, controller, resolved, "close",
                cycle=cycle, cycles=cycles,
                should_stop=should_stop,
            )
            rows.append(row)
            if on_row is not None:
                on_row(row)
            _reset_contacts(
                controller, resolved, closed=True, should_stop=should_stop,
            )
            if cycle < cycles:
                _sleep_interruptible(FAULT_RESET_SETTLE_S, should_stop)
    except MvbcmStopped:
        raise
    finally:
        try:
            _reset_contacts(controller, resolved, closed=True, should_stop=None)
        except Exception:
            pass
        if cleanup:
            try:
                controller.cleanup()
            except Exception:
                pass
    return rows


def _run_fault_op(
    board, fetch, controller, resolved, operation: str, *,
    cycle: int, cycles: int, should_stop=None,
) -> dict:
    label = "FAULT-TRIP" if operation == "trip" else "FAULT-CLOSE"
    field = "trip_total" if operation == "trip" else "close_total"
    stuck = "52B stays low" if operation == "trip" else "52A stays low"
    cycle_tag = (
        f"cycle={cycle}/{cycles}; {label}; {stuck}; "
        f"counter_wait={FAULT_EVENT_WAIT_S:.0f}s"
    )
    try:
        before_trip, before_close, _before_backup = b7_totals(fetch(board))
    except LiveError as exc:
        return _counter_row(
            operation, field, None, None, None, "FAIL",
            f"{cycle_tag}; {exc}", cycle=cycle, label=label,
        )

    before = before_trip if operation == "trip" else before_close
    expect = before + 1
    try:
        _play_fault(controller, resolved, operation, should_stop=should_stop)
    except Exception as exc:
        return _counter_row(
            operation, field, before, expect, None, "FAIL",
            f"{cycle_tag}; drive failed: {exc}", cycle=cycle, label=label,
        )

    _sleep_interruptible(FAULT_EVENT_WAIT_S, should_stop)
    try:
        got_trip, got_close, got_backup = b7_totals(fetch(board))
    except LiveError as exc:
        return _counter_row(
            operation, field, before, expect, None, "FAIL",
            f"{cycle_tag}; {exc}", cycle=cycle, label=label,
        )

    got = got_trip if operation == "trip" else got_close
    ok = got == expect
    note = (
        f"{cycle_tag}; before_trip={before_trip} before_close={before_close} "
        f"after_trip={got_trip} after_close={got_close} after_backup={got_backup}"
    )
    if not ok:
        note = f"expected {expect}, got {got}; {note}"
    return _counter_row(
        operation, field, before, expect, got,
        "PASS" if ok else "FAIL", note, cycle=cycle, label=label,
    )


def _counter_row(
    operation, field, before, expect, measured, result, note, *, cycle, label,
) -> dict:
    row = _result_row(
        operation, field, before, expect, measured, result, note, cycle=cycle,
    )
    row["group"] = "fault_trip_close"
    row["test_id"] = label
    return row


def _play_fault(controller, resolved: dict[str, str], operation: str, should_stop=None) -> None:
    """Same pulse + contact fall as trip/close; skip the second-contact rise."""
    has_backup = "backup_trip_coil" in resolved
    if operation == "trip":
        pulse = "trip_coil"
        fall = "switch_52a"
        start_high = "switch_52a"
        pulse_backup = has_backup
    elif operation == "close":
        pulse = "close_coil"
        fall = "switch_52b"
        start_high = "switch_52b"
        pulse_backup = False
    else:
        raise ValueError(f"unknown operation {operation!r}")

    levels = {key: False for key in resolved}
    levels[start_high] = True

    def apply(keys=None) -> None:
        _raise_if_stopped(should_stop)
        for key in (keys if keys is not None else resolved):
            if key not in resolved:
                continue
            channel = resolved[key]
            if controller.set_output_line(channel, bool(levels[key])):
                continue
            raise RuntimeError(_fail_msg(controller, channel))

    apply()
    _sleep_interruptible(TRIP_PRE_TRIGGER_S, should_stop)
    levels[pulse] = True
    coil_keys = [pulse]
    if pulse_backup:
        levels["backup_trip_coil"] = True
        coil_keys.append("backup_trip_coil")
    apply(coil_keys)
    _sleep_interruptible(TRIP_PULSE_S, should_stop)
    levels[pulse] = False
    if pulse_backup:
        levels["backup_trip_coil"] = False
    levels[fall] = False
    fall_keys = list(coil_keys)
    if fall not in fall_keys:
        fall_keys.append(fall)
    apply(fall_keys)
    _sleep_interruptible(TRIP_OPENING_TRAVEL_S, should_stop)
    # Fault: do not raise the complementary contact.
    _sleep_interruptible(TRIP_POST_TRIGGER_S, should_stop)


def _reset_contacts(
    controller, resolved: dict[str, str], *, closed: bool, should_stop=None,
) -> None:
    """Idle coils. Closed = 52A high / 52B low. Open = 52A low / 52B high."""
    levels = {key: False for key in resolved}
    if closed:
        levels["switch_52a"] = True
    else:
        levels["switch_52b"] = True
    _raise_if_stopped(should_stop)
    for key, channel in resolved.items():
        if levels[key]:
            continue
        if not controller.set_output_line(channel, False):
            raise RuntimeError(_fail_msg(controller, channel))
    high_key = "switch_52a" if closed else "switch_52b"
    if not controller.set_output_line(resolved[high_key], True):
        raise RuntimeError(_fail_msg(controller, resolved[high_key]))


def _fail_msg(controller, channel: str) -> str:
    detail = ""
    getter = getattr(controller.task_manager, "get_last_error", None)
    if getter:
        detail = (getter() or "").strip()
    ports = ""
    port_fn = getattr(controller, "get_last_failed_ports", None)
    if port_fn:
        failed = port_fn()
        if failed:
            ports = f". Set port {failed} to Output in NI MAX"
    return (detail or f"Failed to write {channel}") + ports
