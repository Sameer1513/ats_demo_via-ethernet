"""Trip/close breaker ops scored against board ``b7`` counters.

Applies one trip then one close on the wet DO lines (same timing as the
web Apply trip/close), waits ``COUNTER_SETTLE_S`` (15 s), then checks
``trip_total`` / ``close_total`` each increased by 1 on ``GET /api/live``.
When ``backup_trip_coil`` is mapped it pulses with trip and
``backup_trip_total`` is scored as its own row.

Event check (Events page Refresh list): snapshot ``GET /api/events`` before
the op (wait ``EVENT_REFRESH_WAIT_S``), apply, snapshot again.
Trip with backup_trip_total +1 expects event max_id/count **+2**;
trip without backup or close expects **+1**.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from nidaq_controller.mvbcm.live import LiveError, fetch_live


class MvbcmStopped(Exception):
    """User requested stop of an in-flight MV BCM run."""


def _raise_if_stopped(should_stop) -> None:
    if should_stop is not None and should_stop():
        raise MvbcmStopped("stopped by user")


def _sleep_interruptible(seconds: float, should_stop=None) -> None:
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        _raise_if_stopped(should_stop)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.2, remaining))


def _ensure_daq_path() -> None:
    """daq_io modules import as ``core.*`` / ``constants`` (web-server layout)."""
    root = Path(__file__).resolve().parents[1]
    root_s = str(root)
    if root_s not in sys.path:
        sys.path.insert(0, root_s)

# Same defaults as ui/templates/index.html TRIP_LINE_MAP (wet DI section).
DEFAULT_LINE_MAP = {
    "trip_coil": "port2/line1",          # P2.1 Ch1 DI_13
    "backup_trip_coil": "port5/line1",   # P5.1 Ch2 DI_14
    "switch_52a": "port5/line0",         # P5.0 Ch4 DI_16
    "switch_52b": "port4/line7",         # P4.7 Ch6 DI_18
    "close_coil": "port2/line0",         # P2.0 Ch3 DI_15
}

REQUIRED_CHANNEL_KEYS = ("trip_coil", "switch_52a", "switch_52b", "close_coil")
OPTIONAL_CHANNEL_KEYS = ("backup_trip_coil",)
CHANNEL_KEYS = REQUIRED_CHANNEL_KEYS + OPTIONAL_CHANNEL_KEYS

# Wait after each trip/close apply before reading b7 counters.
COUNTER_SETTLE_S = 15.0
# Gap between finished trip scoring and the close apply.
TRIP_TO_CLOSE_GAP_S = 30.0
# After Refresh-list equivalent (GET /api/events), wait so the board list settles.
EVENT_REFRESH_WAIT_S = 10.0

DEFAULT_CHANNELS_FILE = Path(__file__).resolve().parent / "trip_channels.json"


def load_channels_json(path: Path | str) -> dict[str, str]:
    """Load channel map from JSON (4 required keys + optional backup)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("channels file must be a JSON object")
    missing = [key for key in REQUIRED_CHANNEL_KEYS if not data.get(key)]
    if missing:
        raise ValueError(f"channels file missing keys: {', '.join(missing)}")
    out = {key: str(data[key]) for key in REQUIRED_CHANNEL_KEYS}
    for key in OPTIONAL_CHANNEL_KEYS:
        raw = (data.get(key) or "").strip()
        if raw:
            out[key] = str(data[key])
    return out


def default_trip_channels() -> dict[str, str]:
    """Load ``trip_channels.json`` (required for trip/close mapping)."""
    if not DEFAULT_CHANNELS_FILE.is_file():
        raise FileNotFoundError(
            f"Trip channel map not found: {DEFAULT_CHANNELS_FILE}"
        )
    return load_channels_json(DEFAULT_CHANNELS_FILE)


def device_name_from_channels(channels: dict[str, str]) -> str | None:
    """Device name from the first full DAQmx path (e.g. Dev9/...)."""
    for key in CHANNEL_KEYS:
        raw = (channels.get(key) or "").strip()
        if "/" in raw:
            return raw.split("/", 1)[0]
    return None


def find_digital_module(device_name: str | None = None):
    """Return ModuleInfo for a digital module (USB-6509 or named device)."""
    _ensure_daq_path()
    from core.device_manager import DeviceManager

    manager = DeviceManager()
    devices = manager.discover_devices()
    candidates = []
    for device in devices:
        modules = device.modules or [device]
        for module in modules:
            do_channels = list(getattr(module, "do_channels", None) or [])
            if not do_channels:
                continue
            name = getattr(module, "name", "") or ""
            product = (getattr(module, "product_type", "") or "").upper()
            if device_name and name != device_name and device.name != device_name:
                continue
            score = len(do_channels)
            if "6509" in product or "6509" in name.upper():
                score += 1000
            candidates.append((score, module))
    if device_name and not candidates:
        raise RuntimeError(f"No digital module matching {device_name!r}")
    if not candidates:
        raise RuntimeError(
            "No digital-output module found. Pass --trip-device "
            "(e.g. Dev1) or a --trip-channels JSON with full paths."
        )
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def resolve_channels(
    module,
    line_map: dict[str, str] | None = None,
) -> dict[str, str]:
    """Map role keys to full physical DO channel names on ``module``."""
    mapping = line_map or DEFAULT_LINE_MAP
    known = list(getattr(module, "do_channels", None) or [])
    if not known:
        raise RuntimeError(f"{module.name} has no digital output channels")
    device_name = module.name
    resolved: dict[str, str] = {}
    for key in REQUIRED_CHANNEL_KEYS:
        raw = mapping.get(key) or ""
        hit = _match_line(known, device_name, raw)
        if not hit:
            raise RuntimeError(
                f"Could not resolve {key}={raw!r} on {device_name}. "
                "Pass --trip-channels with full DAQmx paths."
            )
        resolved[key] = hit
    for key in OPTIONAL_CHANNEL_KEYS:
        raw = (mapping.get(key) or "").strip()
        if not raw:
            continue
        hit = _match_line(known, device_name, raw)
        if not hit:
            raise RuntimeError(
                f"Could not resolve {key}={raw!r} on {device_name}. "
                "Pass --trip-channels with full DAQmx paths."
            )
        resolved[key] = hit
    return resolved


def _match_line(known: list[str], device_name: str, raw: str) -> str | None:
    text = (raw or "").strip()
    if not text:
        return None
    if text in known:
        return text
    lower = text.lower().replace("\\", "/")
    # Accept P2.1 / p2.1 style
    if lower.startswith("p") and "." in lower and "/" not in lower:
        try:
            port_s, line_s = lower[1:].split(".", 1)
            lower = f"port{int(port_s)}/line{int(line_s)}"
        except ValueError:
            pass
    if "/" not in lower:
        lower = f"{device_name.lower()}/{lower}"
    elif not lower.startswith(device_name.lower() + "/") and lower.count("/") == 1:
        # portN/lineM
        lower = f"{device_name.lower()}/{lower}"
    for ch in known:
        if ch.lower() == lower or ch.lower().endswith("/" + lower.split("/", 1)[-1]):
            return ch
        if ch.lower().endswith(lower if lower.startswith("/") else "/" + "/".join(lower.split("/")[-2:])):
            return ch
    suffix = "/".join(lower.split("/")[-2:])
    for ch in known:
        if ch.lower().endswith("/" + suffix) or ch.lower().endswith(suffix):
            return ch
    return None


def b7_totals(payload: dict) -> tuple[int, int, int]:
    """Return ``(trip_total, close_total, backup_trip_total)`` from live."""
    b7 = payload.get("b7")
    if not isinstance(b7, dict):
        raise LiveError("live payload has no b7 object")
    try:
        return (
            int(b7["trip_total"]),
            int(b7["close_total"]),
            int(b7["backup_trip_total"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise LiveError(f"b7 trip/close/backup totals missing: {exc}") from exc


def b7_event_stamp(payload: dict) -> int:
    """``b7.last_confirmed_operation_ms`` (0 if missing)."""
    b7 = payload.get("b7")
    if not isinstance(b7, dict):
        raise LiveError("live payload has no b7 object")
    try:
        return int(b7.get("last_confirmed_operation_ms") or 0)
    except (TypeError, ValueError) as exc:
        raise LiveError(f"b7.last_confirmed_operation_ms invalid: {exc}") from exc


def fetch_event_snapshot(board: str, timeout: float = 30.0) -> dict[str, Any] | None:
    """GET ``/api/events`` — same list the Events page Refresh list uses.

    Returns ``{"count": N, "max_id": M, "ids": [...]}`` or None.
    """
    url = board.rstrip("/") + "/api/events"
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError):
        return None
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        return None
    events = payload.get("events")
    if not isinstance(events, list):
        return None
    ids: list[int] = []
    for item in events:
        if not isinstance(item, dict):
            continue
        try:
            ids.append(int(item["id"]))
        except (KeyError, TypeError, ValueError):
            continue
    return {
        "count": len(events),
        "max_id": max(ids) if ids else 0,
        "ids": ids,
    }


def refresh_event_list(
    board: str,
    *,
    wait_s: float = EVENT_REFRESH_WAIT_S,
    should_stop=None,
    timeout: float = 30.0,
) -> dict[str, Any] | None:
    """Refresh-list equivalent: GET events, wait for UI settle, GET again."""
    fetch_event_snapshot(board, timeout=timeout)  # kick refresh
    _sleep_interruptible(max(0.0, wait_s), should_stop)
    return fetch_event_snapshot(board, timeout=timeout)


def wait_counter(
    fetch: Callable[[str], dict],
    board: str,
    *,
    trip_expect: int | None = None,
    close_expect: int | None = None,
    backup_expect: int | None = None,
    timeout_s: float = 10.0,
    poll_s: float = 0.5,
    should_stop=None,
) -> tuple[int, int, int, int]:
    """Poll live until the expected counter(s) match, or timeout.

    Returns ``(trip, close, backup, last_confirmed_operation_ms)``.
    """
    deadline = time.monotonic() + timeout_s
    last = (-1, -1, -1)
    last_stamp = 0
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        _raise_if_stopped(should_stop)
        try:
            payload = fetch(board)
            last = b7_totals(payload)
            last_stamp = b7_event_stamp(payload)
            trip_ok = trip_expect is None or last[0] == trip_expect
            close_ok = close_expect is None or last[1] == close_expect
            backup_ok = backup_expect is None or last[2] == backup_expect
            if trip_ok and close_ok and backup_ok:
                return (*last, last_stamp)
        except LiveError as exc:
            last_error = exc
        time.sleep(poll_s)
    if last_error is not None and last == (-1, -1, -1):
        raise last_error
    return (*last, last_stamp)


def apply_operation(controller, channels: dict[str, str], operation: str) -> dict[str, Any]:
    """Drive one trip or close through ``DigitalIOController``."""
    return controller.apply_breaker_operation(channels, operation)


def open_breaker_controller(module):
    """Build a DigitalIOController for the given module."""
    _ensure_daq_path()
    from core.task_manager import TaskManager
    from daq_io.digital_io import DigitalIOController

    return DigitalIOController(TaskManager(), module)


def run_trip_close_test(
    board: str,
    *,
    device: str | None = None,
    channels: dict[str, str] | None = None,
    settle: float | None = None,
    timeout: float = 5.0,
    cycles: int = 1,
    fetch=None,
    controller_factory=None,
    module=None,
    on_row=None,
    cleanup: bool = True,
    should_stop=None,
) -> list[dict]:
    """Apply trip then close ``cycles`` times; score ``b7`` counters.

    Uses ``DigitalIOController.apply_breaker_operation`` — the same method
    as the web Apply trip / Apply close buttons.
    Waits ``settle`` (default ``COUNTER_SETTLE_S`` = 15 s) after each apply
    before checking counters. ``should_stop`` raises ``MvbcmStopped`` between
    cycles when true.
    """
    if cycles < 1:
        raise ValueError("cycles must be >= 1")
    if settle is None:
        settle = COUNTER_SETTLE_S
    _ensure_daq_path()
    if fetch is None:
        def fetch(url, _timeout=timeout):
            return fetch_live(url, timeout=_timeout)

    resolved = channels or default_trip_channels()
    prefer = device or device_name_from_channels(resolved)
    if module is None:
        module = find_digital_module(prefer)
    # Re-resolve against the module so short or full paths both work.
    resolved = resolve_channels(module, resolved)

    if controller_factory is None:
        controller_factory = open_breaker_controller
    controller = controller_factory(module)

    score_backup = "backup_trip_coil" in resolved
    rows: list[dict] = []
    try:
        for cycle in range(1, cycles + 1):
            _raise_if_stopped(should_stop)
            trip_rows = _run_trip_cycle(
                board, fetch, controller, resolved, settle,
                cycle=cycle, cycles=cycles, score_backup=score_backup,
                should_stop=should_stop,
            )
            for row in trip_rows:
                rows.append(row)
                if on_row is not None:
                    on_row(row)
            _raise_if_stopped(should_stop)
            _sleep_interruptible(TRIP_TO_CLOSE_GAP_S, should_stop)
            _raise_if_stopped(should_stop)
            close_rows = _run_close_cycle(
                board, fetch, controller, resolved, settle,
                cycle=cycle, cycles=cycles,
                should_stop=should_stop,
            )
            for row in close_rows:
                rows.append(row)
                if on_row is not None:
                    on_row(row)
    finally:
        if cleanup:
            try:
                controller.cleanup()
            except Exception:
                pass
    return rows


def _event_fail_rows(
    cycle_tag: str, cycle: int, exc: Exception, *, score_backup: bool = False,
) -> list[dict]:
    rows = [
        _result_row(
            "trip", "trip_total", None, None, None, "FAIL",
            f"{cycle_tag}; {exc}", cycle=cycle,
        ),
    ]
    if score_backup:
        rows.append(
            _result_row(
                "backup_trip", "backup_trip_total", None, None, None, "FAIL",
                f"{cycle_tag}; {exc}", cycle=cycle,
            )
        )
    rows.append(
        _result_row(
            "trip", "event_created", None, None, None, "FAIL",
            f"{cycle_tag}; {exc}", cycle=cycle, phase="event", unit="event",
        )
    )
    return rows


def _score_event_row(
    *,
    operation: str,
    cycle: int,
    cycle_tag: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    expect_delta: int = 1,
    wait_s: float | None = None,
) -> dict:
    """PASS if max_id or count advances by exactly ``expect_delta``.

    Trip + backup_trip_total bump → expect_delta=2 (Event box +2).
    Close / trip-only → expect_delta=1.
    Prefer max_id (matches ``event_000NN`` on the Events page).
    """
    before_count = None if before is None else int(before["count"])
    after_count = None if after is None else int(after["count"])
    before_id = None if before is None else int(before["max_id"])
    after_id = None if after is None else int(after["max_id"])
    delta = max(1, int(expect_delta))
    wait = EVENT_REFRESH_WAIT_S if wait_s is None else float(wait_s)
    id_ok = (
        before_id is not None
        and after_id is not None
        and after_id == before_id + delta
    )
    count_ok = (
        before_count is not None
        and after_count is not None
        and after_count == before_count + delta
    )
    ok = id_ok or count_ok
    measured = after_id if after_id is not None else after_count
    expect = (before_id + delta) if before_id is not None else (
        (before_count + delta) if before_count is not None else None
    )
    note = (
        f"{cycle_tag}; refresh_wait={wait:.0f}s; "
        f"expect_delta={delta}; "
        f"events {before_count}->{after_count}; max_id {before_id}->{after_id}"
    )
    if before is None or after is None:
        ok = False
        note = f"/api/events unavailable; {note}"
    elif not ok:
        got_id = (
            None if before_id is None or after_id is None
            else after_id - before_id
        )
        got_count = (
            None if before_count is None or after_count is None
            else after_count - before_count
        )
        note = (
            f"expected +{delta} event(s), got max_id_delta={got_id} "
            f"count_delta={got_count}; {note}"
        )
    return _result_row(
        operation, "event_created", before_id, expect, measured,
        "PASS" if ok else "FAIL", note, cycle=cycle,
        phase="event", unit="event",
    )


def _run_trip_cycle(
    board, fetch, controller, channels, settle,
    *, cycle: int, cycles: int, score_backup: bool, should_stop=None,
) -> list[dict]:
    """One trip: snapshot events → trip → wait settle → score trip/backup/event."""
    cycle_tag = f"cycle={cycle}/{cycles}"
    try:
        before_payload = fetch(board)
        before_trip, before_close, before_backup = b7_totals(before_payload)
    except LiveError as exc:
        return _event_fail_rows(cycle_tag, cycle, exc, score_backup=score_backup)

    # Events page: Refresh list → wait → read event box count / id.
    before_events = refresh_event_list(board, should_stop=should_stop)
    trip_expect = before_trip + 1
    backup_expect = before_backup + 1 if score_backup else None
    try:
        apply_operation(controller, channels, "trip")
    except Exception as exc:
        rows = [
            _result_row(
                "trip", "trip_total", before_trip, trip_expect, None, "FAIL",
                f"{cycle_tag}; {exc}", cycle=cycle,
            ),
        ]
        if score_backup:
            rows.append(
                _result_row(
                    "backup_trip", "backup_trip_total", before_backup, backup_expect,
                    None, "FAIL", f"{cycle_tag}; {exc}", cycle=cycle,
                )
            )
        rows.append(
            _result_row(
                "trip", "event_created",
                None if before_events is None else before_events["count"],
                None, None, "FAIL",
                f"{cycle_tag}; {exc}", cycle=cycle, phase="event", unit="event",
            )
        )
        return rows

    _sleep_interruptible(max(0.0, settle), should_stop)
    timeout_s = max(10.0, settle + 5.0)
    try:
        got_trip, got_close, got_backup, _ = wait_counter(
            fetch, board,
            trip_expect=trip_expect,
            backup_expect=backup_expect,
            timeout_s=timeout_s,
            should_stop=should_stop,
        )
    except LiveError as exc:
        return _event_fail_rows(cycle_tag, cycle, exc, score_backup=score_backup)

    # Refresh list again after trip so the new event shows in the box.
    after_events = refresh_event_list(board, should_stop=should_stop)

    note = (
        f"{cycle_tag}; settle={settle:.0f}s; "
        f"before_trip={before_trip} before_backup={before_backup} "
        f"after_trip={got_trip} after_backup={got_backup} after_close={got_close}"
    )
    trip_ok = got_trip == trip_expect
    trip_note = note if trip_ok else f"expected {trip_expect}, got {got_trip}; {note}"
    rows = [
        _result_row(
            "trip", "trip_total", before_trip, trip_expect, got_trip,
            "PASS" if trip_ok else "FAIL", trip_note, cycle=cycle,
        )
    ]
    if score_backup:
        backup_ok = got_backup == backup_expect
        backup_note = (
            note if backup_ok
            else f"expected {backup_expect}, got {got_backup}; {note}"
        )
        rows.append(
            _result_row(
                "backup_trip", "backup_trip_total", before_backup, backup_expect,
                got_backup, "PASS" if backup_ok else "FAIL", backup_note,
                cycle=cycle,
            )
        )
    # Backup counter +1 on this trip → board stores +2 events (trip + backup).
    backup_bumped = score_backup and got_backup == backup_expect
    event_delta = 2 if backup_bumped else 1
    rows.append(
        _score_event_row(
            operation="trip",
            cycle=cycle,
            cycle_tag=cycle_tag,
            before=before_events,
            after=after_events,
            expect_delta=event_delta,
        )
    )
    return rows


def _run_close_cycle(
    board, fetch, controller, channels, settle,
    *, cycle: int = 1, cycles: int = 1, should_stop=None,
) -> list[dict]:
    """One close: snapshot events → close → wait settle → score close + event."""
    cycle_tag = f"cycle={cycle}/{cycles}"
    try:
        before_trip, before_close, before_backup = b7_totals(fetch(board))
    except LiveError as exc:
        return [
            _result_row(
                "close", "close_total", None, None, None, "FAIL",
                f"{cycle_tag}; {exc}", cycle=cycle,
            ),
            _result_row(
                "close", "event_created", None, None, None, "FAIL",
                f"{cycle_tag}; {exc}", cycle=cycle, phase="event", unit="event",
            ),
        ]

    before_events = refresh_event_list(board, should_stop=should_stop)
    expect = before_close + 1
    try:
        apply_operation(controller, channels, "close")
    except Exception as exc:
        return [
            _result_row(
                "close", "close_total", before_close, expect, None, "FAIL",
                f"{cycle_tag}; {exc}", cycle=cycle,
            ),
            _result_row(
                "close", "event_created",
                None if before_events is None else before_events["max_id"],
                None, None, "FAIL",
                f"{cycle_tag}; {exc}", cycle=cycle, phase="event", unit="event",
            ),
        ]

    _sleep_interruptible(max(0.0, settle), should_stop)
    try:
        got_trip, got_close, got_backup, _got_stamp = wait_counter(
            fetch, board, close_expect=expect, timeout_s=max(10.0, settle + 5.0),
            should_stop=should_stop,
        )
        note = (
            f"{cycle_tag}; settle={settle:.0f}s; before={before_close} "
            f"after_trip={got_trip} after_backup={got_backup} after_close={got_close}"
        )
    except LiveError as exc:
        return [
            _result_row(
                "close", "close_total", before_close, expect, None, "FAIL",
                f"{cycle_tag}; {exc}", cycle=cycle,
            ),
            _result_row(
                "close", "event_created",
                None if before_events is None else before_events["max_id"],
                None, None, "FAIL",
                f"{cycle_tag}; {exc}", cycle=cycle, phase="event", unit="event",
            ),
        ]

    close_ok = got_close == expect
    if not close_ok:
        note = f"expected {expect}, got {got_close}; {note}"
    after_events = refresh_event_list(board, should_stop=should_stop)
    return [
        _result_row(
            "close", "close_total", before_close, expect, got_close,
            "PASS" if close_ok else "FAIL", note, cycle=cycle,
        ),
        _score_event_row(
            operation="close",
            cycle=cycle,
            cycle_tag=cycle_tag,
            before=before_events,
            after=after_events,
            expect_delta=1,
        ),
    ]


def _result_row(
    operation, signal, before, expect, measured, result, note, *,
    cycle: int = 1, phase: str = "counter", unit: str = "count",
) -> dict:
    return {
        "group": "trip_close",
        "test_id": "B7-OPS",
        "step": f"{operation}-{cycle}",
        "signal": signal,
        "phase": phase,
        "stimulus_value": "1",
        "unit": unit,
        "measured": "" if measured is None else str(int(measured)),
        "field": "b7",
        "min": "" if expect is None else str(int(expect)),
        "max": "" if expect is None else str(int(expect)),
        "result": result,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "note": note if before is None else (note or f"before={before}"),
    }
