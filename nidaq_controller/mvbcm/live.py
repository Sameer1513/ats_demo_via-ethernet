"""Read the board live API and pick the engineering field for a DUT unit."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

# DUT unit text from the sheet, compared after collapsing whitespace
# and upper-casing. More units are more entries, not a new reader.
DUT_FIELD = {
    "A": "rms_eng",
    "A RMS": "rms_eng",
    "A PEAK": "peak_eng",
}


class LiveError(Exception):
    """The live payload cannot be scored."""


def fetch_live(board: str, timeout: float = 5.0) -> dict:
    """GET ``{board}/api/live`` and require ``ok`` true."""
    url = board.rstrip("/") + "/api/live"
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise LiveError(f"GET {url} failed: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise LiveError(f"GET {url} returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise LiveError(f"GET {url} did not return an object")
    if payload.get("ok") is not True:
        raise LiveError(f"live ok is not true: {payload.get('ok')!r}")
    return payload


def field_for_unit(dut_unit: str) -> str:
    """Map a sheet DUT unit onto a key under ``rms``."""
    key = " ".join((dut_unit or "").strip().upper().split())
    try:
        return DUT_FIELD[key]
    except KeyError as exc:
        raise LiveError(f"no live field mapped for DUT unit {dut_unit!r}") from exc


def measure(payload: dict, signal: str, field: str) -> float:
    """Return ``rms[field]`` for the channel whose ``signal`` matches."""
    rms = payload.get("rms")
    if not isinstance(rms, dict):
        raise LiveError("live payload has no rms object")
    channels = rms.get("channels")
    if not isinstance(channels, list):
        raise LiveError("live payload has no rms.channels")
    index = None
    for i, channel in enumerate(channels):
        if not isinstance(channel, dict):
            continue
        if str(channel.get("signal", "")).lower() == signal.lower():
            index = i
            break
    if index is None:
        raise LiveError(f"signal {signal!r} not in live channels")
    values = rms.get(field)
    if not isinstance(values, list) or index >= len(values):
        raise LiveError(f"field {field} missing at index {index}")
    try:
        return float(values[index])
    except (TypeError, ValueError) as exc:
        raise LiveError(f"field {field}[{index}] is not a number") from exc


def in_limits(measured: float, low: float, high: float) -> bool:
    """Inclusive window."""
    return low <= measured <= high


def operations_recent(payload: dict) -> list[dict]:
    """Rows under ``operations.recent``, newest-last as published."""
    ops = payload.get("operations")
    if not isinstance(ops, dict):
        return []
    recent = ops.get("recent")
    if not isinstance(recent, list):
        return []
    return [row for row in recent if isinstance(row, dict)]


def max_recent_ts(payload: dict) -> int:
    """Highest ``operations.recent[].ts_ms``, or 0 when the list is empty."""
    best = 0
    for row in operations_recent(payload):
        try:
            best = max(best, int(row.get("ts_ms") or 0))
        except (TypeError, ValueError):
            continue
    return best


def pick_recent_row(payload: dict, before_ts: int | None, slots: tuple[str, ...]) -> dict | None:
    """Newest row with ``ts_ms`` after ``before_ts``. Earlier slots win.

    ``before_ts`` None means the pre-command snapshot failed, so no row
    is treated as new.
    """
    if before_ts is None:
        return None
    chosen = None
    chosen_rank = len(slots)
    chosen_ts = -1
    wanted = {slot.lower(): index for index, slot in enumerate(slots)}
    for row in operations_recent(payload):
        slot = str(row.get("rec_slot") or "").lower()
        if slot not in wanted:
            continue
        try:
            ts = int(row.get("ts_ms") or 0)
        except (TypeError, ValueError):
            continue
        if ts <= before_ts:
            continue
        rank = wanted[slot]
        if rank < chosen_rank or (rank == chosen_rank and ts >= chosen_ts):
            chosen = row
            chosen_rank = rank
            chosen_ts = ts
    return chosen


def wait_for_recent_row(
    fetch,
    board: str,
    before_ts: int | None,
    slots: tuple[str, ...],
    timeout_s: float = 30.0,
    poll_s: float = 1.0,
    should_stop=None,
    stop_error: type[Exception] | None = None,
    fallback_slots: tuple[str, ...] = (),
) -> tuple[dict, dict]:
    """Poll until a new ``operations.recent`` row matches ``slots``.

    ``fallback_slots`` are used only when the wait ends with no primary
    row. A fault row must not hide a trip row that is saved a moment later.
    Returns ``(payload, row)``. Raises ``stop_error`` when ``should_stop``
    is set, or ``LiveError`` when the wait ends with no matching row.
    """
    import time

    if before_ts is None:
        raise LiveError("operations.recent was not read before the command")
    deadline = time.monotonic() + timeout_s
    last_error: Exception | None = None
    fallback: tuple[dict, dict] | None = None
    while time.monotonic() < deadline:
        if should_stop is not None and should_stop():
            if stop_error is not None:
                raise stop_error("stopped by user")
            raise LiveError("stopped by user")
        try:
            payload = fetch(board)
        except LiveError as exc:
            last_error = exc
            time.sleep(poll_s)
            continue
        row = pick_recent_row(payload, before_ts, slots)
        if row is not None:
            return payload, row
        if fallback_slots:
            alt = pick_recent_row(payload, before_ts, fallback_slots)
            if alt is not None:
                fallback = (payload, alt)
        time.sleep(poll_s)
    if fallback is not None:
        return fallback
    if last_error is not None:
        raise last_error
    names = ", ".join(slots)
    raise LiveError(f"no new operations.recent row for {names}")


def recent_number(row: dict, key: str) -> float:
    """One numeric field on an ``operations.recent`` row."""
    try:
        return float(row[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise LiveError(f"operations.recent.{key} missing") from exc


def b7_value(payload: dict, key: str):
    """One field from ``b7``."""
    b7 = payload.get("b7")
    if not isinstance(b7, dict):
        raise LiveError("live payload has no b7 object")
    if key not in b7:
        raise LiveError(f"b7.{key} missing")
    return b7[key]


def digital_active(payload: dict, signal: str) -> bool:
    """``digital[].active`` for ``signal`` (case-insensitive, punctuation ignored)."""
    digital = payload.get("digital")
    if not isinstance(digital, list):
        raise LiveError("live payload has no digital list")
    wanted = _norm_signal(signal)
    for entry in digital:
        if not isinstance(entry, dict):
            continue
        if _norm_signal(str(entry.get("signal") or "")) != wanted:
            continue
        return _is_active(entry.get("active"))
    raise LiveError(f"signal {signal!r} not in live digital")


def _norm_signal(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _is_active(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "active")
    return False
