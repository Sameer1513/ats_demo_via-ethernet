"""USB2 DI Wet mapping check vs board ``digital[].active``.

Drives wet DO Ch1 / 2 / 3 / 4 / 6 one at a time (trip, backtrip, close,
52A, 52B). Each cycle: TRUE → wait 5 s → score live ``active``; FALSE →
wait 5 s → score inactive. Other digital bits must stay inactive.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from nidaq_controller.mvbcm.breaker_ops import (
    MvbcmStopped,
    _raise_if_stopped,
    _sleep_interruptible,
    default_trip_channels,
    device_name_from_channels,
    find_digital_module,
    open_breaker_controller,
    resolve_channels,
)
from nidaq_controller.mvbcm.live import LiveError, fetch_live

DI_SETTLE_S = 5.0

# USB2 DI Wet (skip Ch5 P1.7). Board digital ids 1..5.
DI_CHECK_LINES: tuple[dict[str, Any], ...] = (
    {
        "usb_ch": 1,
        "coil_key": "trip_coil",
        "signal": "trip",
        "board_id": 1,
        "aliases": ("trip",),
    },
    {
        "usb_ch": 2,
        "coil_key": "backup_trip_coil",
        "signal": "backtrip",
        "board_id": 2,
        "aliases": ("backtrip", "backuptrip", "backup"),
    },
    {
        "usb_ch": 3,
        "coil_key": "close_coil",
        "signal": "close",
        "board_id": 3,
        "aliases": ("close",),
    },
    {
        "usb_ch": 4,
        "coil_key": "switch_52a",
        "signal": "52a",
        "board_id": 4,
        "aliases": ("52a", "switch52a", "a52"),
    },
    {
        "usb_ch": 6,
        "coil_key": "switch_52b",
        "signal": "52b",
        "board_id": 5,
        "aliases": ("52b", "switch52b", "b52"),
    },
)


def _read_settled(fetch, board: str, settle: float, should_stop=None):
    from nidaq_controller.mvbcm.run_mvbcm import _read_settled as _impl
    return _impl(fetch, board, settle, should_stop=should_stop)


def run_di_check_test(
    board: str,
    *,
    device: str | None = None,
    channels: dict[str, str] | None = None,
    settle: float = DI_SETTLE_S,
    timeout: float = 5.0,
    cycles: int = 1,
    fetch=None,
    controller_factory=None,
    module=None,
    on_row=None,
    cleanup: bool = True,
    should_stop=None,
) -> list[dict]:
    """Toggle wet DI lines ``cycles`` times; score board digital ``active``."""
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

    lines = [spec for spec in DI_CHECK_LINES if spec["coil_key"] in resolved]
    missing = [
        spec["signal"]
        for spec in DI_CHECK_LINES
        if spec["coil_key"] not in resolved
    ]
    rows: list[dict] = []
    try:
        _write_all(controller, resolved, False)
        for cycle in range(1, cycles + 1):
            for spec in lines:
                _raise_if_stopped(should_stop)
                high_row = _run_level(
                    board, fetch, controller, resolved, spec,
                    want_active=True, settle=settle, cycle=cycle, cycles=cycles,
                    should_stop=should_stop,
                )
                rows.append(high_row)
                if on_row is not None:
                    on_row(high_row)
                _raise_if_stopped(should_stop)
                low_row = _run_level(
                    board, fetch, controller, resolved, spec,
                    want_active=False, settle=settle, cycle=cycle, cycles=cycles,
                    should_stop=should_stop,
                )
                rows.append(low_row)
                if on_row is not None:
                    on_row(low_row)
        for signal in missing:
            row = _row(
                cycle=1, usb_ch="-", signal=signal, phase="map",
                stimulus="false", measured=None, result="FAIL",
                note=f"{signal} coil not in trip_channels.json",
            )
            rows.append(row)
            if on_row is not None:
                on_row(row)
    except MvbcmStopped:
        raise
    finally:
        try:
            _write_all(controller, resolved, False)
        except Exception:
            pass
        if cleanup:
            try:
                controller.cleanup()
            except Exception:
                pass
    return rows


def _run_level(
    board, fetch, controller, resolved, spec, *,
    want_active: bool, settle: float, cycle: int, cycles: int, should_stop=None,
) -> dict:
    usb_ch = int(spec["usb_ch"])
    signal = str(spec["signal"])
    coil_key = str(spec["coil_key"])
    phase = "high" if want_active else "low"
    stimulus = "true" if want_active else "false"
    cycle_tag = f"cycle {cycle}/{cycles}; USB Ch{usb_ch} {signal}"
    try:
        _apply_pattern(controller, resolved, coil_key if want_active else None)
    except Exception as exc:
        return _row(
            cycle=cycle, usb_ch=usb_ch, signal=signal, phase=phase,
            stimulus=stimulus, measured=None, result="FAIL",
            note=f"{cycle_tag}; DO write failed: {exc}",
        )

    try:
        payload = _read_settled(fetch, board, settle, should_stop=should_stop)
        got, extras = _score_digital(payload, spec, want_active=want_active)
    except LiveError as exc:
        return _row(
            cycle=cycle, usb_ch=usb_ch, signal=signal, phase=phase,
            stimulus=stimulus, measured=None, result="FAIL",
            note=f"{cycle_tag}; {exc}",
        )
    except MvbcmStopped:
        raise

    expect = "1" if want_active else "0"
    got_s = "1" if got else "0"
    ok = got == want_active and not extras
    note = (
        f"{cycle_tag}; board digital.{signal} active={got_s} "
        f"(expect {expect}); settle={settle:.0f}s"
    )
    if extras:
        note += f"; extra active: {', '.join(extras)}"
    if not ok:
        note = (
            f"expected {signal} active={expect}"
            + (f", extras {extras}" if extras else "")
            + f", got {got_s}; {note}"
        )
    return _row(
        cycle=cycle, usb_ch=usb_ch, signal=signal, phase=phase,
        stimulus=stimulus, measured=got_s, result="PASS" if ok else "FAIL",
        note=note, expect=expect,
    )


def _write_failed(controller, key: str, channel: str) -> RuntimeError:
    ports = ""
    getter = getattr(controller, "get_last_failed_ports", None)
    if getter:
        failed = getter()
        if failed:
            ports = f". Set port {failed} to Output in NI MAX"
    return RuntimeError(f"Failed to write {key}={channel}{ports}")


def _write_all(controller, resolved: dict[str, str], state: bool) -> None:
    for key, channel in resolved.items():
        if not controller.set_output_line(channel, state):
            raise _write_failed(controller, key, channel)


def _apply_pattern(controller, resolved: dict[str, str], high_key: str | None) -> None:
    """Drive one coil high (or all low). Others first so port2 cannot glitch."""
    for key, channel in resolved.items():
        if key == high_key:
            continue
        if not controller.set_output_line(channel, False):
            raise _write_failed(controller, key, channel)
    if high_key is None:
        return
    channel = resolved[high_key]
    if not controller.set_output_line(channel, True):
        raise _write_failed(controller, high_key, channel)


def _norm(text: str) -> str:
    return "".join(ch for ch in (text or "").lower() if ch.isalnum())


def _is_active(entry: dict) -> bool:
    value = entry.get("active")
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "active")
    return False


def _digital_entries(payload: dict) -> list[dict]:
    digital = payload.get("digital")
    if not isinstance(digital, list):
        raise LiveError("live payload has no digital list")
    return [item for item in digital if isinstance(item, dict)]


def _match_entry(entries: list[dict], spec: dict) -> dict | None:
    aliases = {_norm(a) for a in spec["aliases"]}
    aliases.add(_norm(spec["signal"]))
    for entry in entries:
        sig = _norm(str(entry.get("signal") or ""))
        name = _norm(str(entry.get("name") or ""))
        if sig in aliases or name in aliases:
            return entry
    board_id = int(spec["board_id"])
    for entry in entries:
        try:
            if int(entry.get("id")) == board_id:
                return entry
        except (TypeError, ValueError):
            continue
    return None


def _entry_label(entry: dict) -> str:
    signal = str(entry.get("signal") or "").strip()
    if signal:
        return signal
    try:
        return f"id{int(entry.get('id'))}"
    except (TypeError, ValueError):
        return "?"


def _score_digital(
    payload: dict, spec: dict, *, want_active: bool,
) -> tuple[bool, list[str]]:
    entries = _digital_entries(payload)
    matched = _match_entry(entries, spec)
    if matched is None:
        raise LiveError(
            f"signal {spec['signal']!r} not in live digital "
            f"(id {spec['board_id']})"
        )
    got = _is_active(matched)
    extras = []
    for entry in entries:
        if entry is matched:
            continue
        if not _is_active(entry):
            continue
        extras.append(_entry_label(entry))
    return got, extras


def _row(
    *,
    cycle: int,
    usb_ch,
    signal: str,
    phase: str,
    stimulus: str,
    measured: str | None,
    result: str,
    note: str,
    expect: str = "",
) -> dict:
    return {
        "group": "di_check",
        "test_id": "DI-WET",
        "step": f"{usb_ch}-{cycle}",
        "signal": signal,
        "phase": phase,
        "stimulus_value": stimulus,
        "unit": "active",
        "measured": "" if measured is None else measured,
        "field": "digital.active",
        "min": expect,
        "max": expect,
        "result": result,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "note": note,
    }
