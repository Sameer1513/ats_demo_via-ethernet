"""Run enabled MV BCM channel groups against the board live API.

Examples::

    .\\.venv\\Scripts\\python.exe -m nidaq_controller.mvbcm.run_mvbcm --board http://192.168.x.x:8080
    .\\.venv\\Scripts\\python.exe -m nidaq_controller.mvbcm.run_mvbcm --board http://192.168.x.x:8080 --device cDAQ1Mod2
    .\\.venv\\Scripts\\python.exe -m nidaq_controller.mvbcm.run_mvbcm --board http://192.168.x.x:8080 --trip-close
    .\\.venv\\Scripts\\python.exe -m nidaq_controller.mvbcm.run_mvbcm --board http://192.168.x.x:8080 --trip-close --trip-close-cycles 5
    .\\.venv\\Scripts\\python.exe -m nidaq_controller.mvbcm.run_mvbcm --board http://192.168.x.x:8080 --trip-close-only --trip-device Dev1
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from nidaq_controller.mvbcm.breaker_ops import (
    default_trip_channels,
    device_name_from_channels,
    load_channels_json,
    run_trip_close_test,
)
from nidaq_controller.mvbcm.channel_map import ChannelGroup, MappedChannel, enabled_groups
from nidaq_controller.mvbcm.live import LiveError, fetch_live, field_for_unit, in_limits, measure
from nidaq_controller.mvbcm.sheet import DEFAULT_WORKBOOK, Step, load_steps, objective_has_label
from nidaq_controller.mvbcm.sweep import steps_for_sweep
from nidaq_controller.mvbcm.stimuli.current import CurrentStimulus, find_9266
from nidaq_controller.mvbcm.stimuli.digital import DigitalStimulus
from nidaq_controller.mvbcm.stimuli.voltage import VoltageStimulus, find_9264

CSV_COLUMNS = (
    "group",
    "test_id",
    "step",
    "signal",
    "phase",
    "stimulus_value",
    "unit",
    "measured",
    "field",
    "min",
    "max",
    "result",
    "timestamp",
    "note",
)

DEFAULT_RESULTS = Path(__file__).resolve().parents[1] / "logs" / "mvbcm_results.csv"
# live.json is rewritten about once a second. One second is not enough
# for a new DC window mean to be published.
DEFAULT_SETTLE_S = 3.0
_LIVE_POLL_S = 1.0


def steps_for_channel(steps: list[Step], channel: MappedChannel) -> list[Step]:
    """Sheet rows this channel is responsible for.

    A channel with a sweep name uses the generated grid instead of the
    coarse workbook points.
    """
    generated = steps_for_sweep(channel)
    if generated is not None:
        return generated
    matched = [
        step
        for step in steps
        if step.test_id in channel.test_ids
        and objective_has_label(step.objective, channel.sheet_label)
        and (
            not channel.input_unit
            or step.input_unit.strip().lower() == channel.input_unit.lower()
        )
    ]
    matched.sort(key=lambda step: (step.step_number, step.row))
    return matched


def open_stimulus(group: ChannelGroup, device_name: str):
    """Build the driver for a group. Voltage and digital raise until enabled."""
    if not group.channels:
        raise RuntimeError(f"Group {group.id!r} has no channels")
    if group.kind == "current":
        return CurrentStimulus(device_name, group.channels)
    if group.kind == "voltage":
        return VoltageStimulus(device_name, group.channels)
    if group.kind == "digital":
        return DigitalStimulus(device_name, group.channels)
    raise RuntimeError(f"Group {group.id!r} has no stimulus kind")


def resolve_device(group: ChannelGroup, device_arg: str | None) -> str:
    if group.device:
        return group.device
    if group.kind == "voltage":
        return find_9264()
    if device_arg:
        return device_arg
    if group.kind == "current":
        return find_9266()
    return device_arg or ""


def run_groups(
    groups: list[ChannelGroup],
    steps: list[Step],
    *,
    board: str,
    device: str | None,
    settle: float,
    timeout: float,
    fetch=None,
    stimulus_opener=None,
    on_row=None,
) -> list[dict]:
    """Drive each group, score the live reading, and return result rows.

    ``on_row`` is called with each result dict as soon as it is scored.
    """
    if fetch is None:
        def fetch(url, _timeout=timeout):
            return fetch_live(url, timeout=_timeout)

    if stimulus_opener is None:
        stimulus_opener = open_stimulus

    rows: list[dict] = []
    for group in groups:
        if not any(steps_for_channel(steps, channel) for channel in group.channels):
            raise RuntimeError(f"Group {group.id!r} matched no sheet rows")
        device_name = resolve_device(group, device)
        print(f"group {group.id} kind={group.kind or 'unset'} device={device_name or '-'}")
        stimulus = None
        try:
            stimulus = stimulus_opener(group, device_name)
            rows.extend(
                _run_group(group, steps, stimulus, board, settle, fetch, on_row)
            )
        finally:
            if stimulus is not None:
                stimulus.close()
    return rows


def _run_group(group, steps, stimulus, board, settle, fetch, on_row) -> list[dict]:
    rows: list[dict] = []
    rows.extend(_run_individual(group, steps, stimulus, board, settle, fetch, on_row))
    rows.extend(_run_simultaneous(group, steps, stimulus, board, settle, fetch, on_row))
    return rows


def _record(rows: list[dict], row: dict, on_row) -> None:
    rows.append(row)
    _print_result(row)
    if on_row is not None:
        on_row(row)


def _run_individual(group, steps, stimulus, board, settle, fetch, on_row) -> list[dict]:
    rows: list[dict] = []
    for channel in group.channels:
        for step in steps_for_channel(steps, channel):
            blocked = _apply_block(group, step)
            if blocked:
                _record(rows, _row(group, channel, step, "individual", None, "", "FAIL", blocked), on_row)
                continue
            values = {item.signal: 0.0 for item in group.channels}
            values[channel.signal] = step.input_value
            try:
                stimulus.write(values, frequency=_frequency_hz(step))
            except Exception as exc:
                _record(rows, _row(group, channel, step, "individual", None, "", "FAIL", str(exc)), on_row)
                continue
            try:
                payload = _read_settled(fetch, board, settle)
            except LiveError as exc:
                _record(rows, _row(group, channel, step, "individual", None, "", "FAIL", str(exc)), on_row)
                continue
            _record(rows, _score_payload(group, channel, step, "individual", payload), on_row)
    return rows


def _run_simultaneous(group, steps, stimulus, board, settle, fetch, on_row) -> list[dict]:
    buckets: dict[str, list[tuple[MappedChannel, Step]]] = {}
    for channel in group.channels:
        for step in steps_for_channel(steps, channel):
            buckets.setdefault(step.step, []).append((channel, step))

    rows: list[dict] = []
    ordered = sorted(buckets, key=lambda label: (_step_sort(label), label))
    for label in ordered:
        pairs = buckets[label]
        values = {item.signal: 0.0 for item in group.channels}
        ready: list[tuple[MappedChannel, Step]] = []
        for channel, step in pairs:
            blocked = _apply_block(group, step)
            if blocked:
                _record(rows, _row(group, channel, step, "simultaneous", None, "", "FAIL", blocked), on_row)
                continue
            values[channel.signal] = step.input_value
            ready.append((channel, step))
        if not ready:
            continue
        try:
            stimulus.write(values, frequency=_frequency_hz(ready[0][1]))
        except Exception as exc:
            for channel, step in ready:
                _record(rows, _row(group, channel, step, "simultaneous", None, "", "FAIL", str(exc)), on_row)
            continue
        try:
            payload = _read_settled(fetch, board, settle)
        except LiveError as exc:
            for channel, step in ready:
                _record(rows, _row(group, channel, step, "simultaneous", None, "", "FAIL", str(exc)), on_row)
            continue
        for channel, step in ready:
            _record(rows, _score_payload(group, channel, step, "simultaneous", payload), on_row)
    return rows


def _live_ts(payload) -> int | None:
    try:
        return int(payload.get("ts_ms"))
    except (TypeError, ValueError, AttributeError):
        return None


def _read_settled(fetch, board: str, settle: float):
    """Wait out the live export, then return a sample newer than the write.

    The board publishes `/api/live` about once a second. Reading immediately
    scores the previous window.
    """
    baseline = None
    try:
        baseline = _live_ts(fetch(board))
    except LiveError:
        baseline = None
    time.sleep(settle)
    deadline = time.monotonic() + 4.0
    last_error = None
    while True:
        payload = None
        try:
            payload = fetch(board)
        except LiveError as exc:
            last_error = exc
        if payload is not None:
            ts = _live_ts(payload)
            if baseline is None or ts is None or ts > baseline:
                return payload
        if time.monotonic() >= deadline:
            if last_error is not None and payload is None:
                raise last_error
            raise LiveError("live sample did not update after the output change")
        time.sleep(_LIVE_POLL_S)


def _score_payload(group, channel, step, phase, payload) -> dict:
    try:
        field = field_for_unit(step.dut_unit)
        measured = measure(payload, channel.signal, field)
    except LiveError as exc:
        field = ""
        try:
            field = field_for_unit(step.dut_unit)
        except LiveError:
            pass
        return _row(group, channel, step, phase, None, field, "FAIL", str(exc))
    result = "PASS" if in_limits(measured, step.min_accepted, step.max_accepted) else "FAIL"
    return _row(group, channel, step, phase, measured, field, result, "")


def _apply_block(group: ChannelGroup, step: Step) -> str:
    """Why this row cannot be driven. Empty string means apply it."""
    if step.input_value is None or step.min_accepted is None or step.max_accepted is None:
        return "row has no input value or limits"
    if group.kind == "current" and step.input_unit.strip().lower() != "ma":
        return f"current group expects mA, got {step.input_unit!r}"
    if group.kind == "voltage" and not step.input_unit.strip().lower().startswith("mv"):
        return f"voltage group expects mV, got {step.input_unit!r}"
    return ""


def _frequency_hz(step: Step) -> float:
    """Hz named in the step text. Sheet rows that omit it stay at 50 Hz."""
    match = re.search(r"(\d+(?:\.\d+)?)\s*Hz", step.condition or "", flags=re.IGNORECASE)
    if not match:
        return 50.0
    return float(match.group(1))


def _row(group, channel, step, phase, measured, field, result, note) -> dict:
    return {
        "group": group.id,
        "test_id": step.test_id,
        "step": step.step,
        "signal": channel.signal,
        "phase": phase,
        "stimulus_value": _fmt(step.input_value),
        "unit": step.input_unit,
        "measured": "" if measured is None else _fmt(measured),
        "field": field,
        "min": _fmt(step.min_accepted),
        "max": _fmt(step.max_accepted),
        "result": result,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "note": note,
    }


def _print_result(row: dict) -> None:
    measured = row["measured"] if row["measured"] != "" else "-"
    note = f" ({row['note']})" if row["note"] else ""
    print(
        f"{row['result']:4} {row['test_id']} step {row['step']} {row['signal']} "
        f"{row['phase']} {row['stimulus_value']} {row['unit']} "
        f"measured {measured} {row['field']} "
        f"[{row['min']}, {row['max']}]{note}",
        flush=True,
    )


def append_results(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        writer.writerows(rows)


def _fmt(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.6g}"


def _step_sort(label: str) -> int:
    try:
        return int(float(label))
    except (TypeError, ValueError):
        return 10**9


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run enabled MV BCM channel groups.")
    parser.add_argument(
        "--board",
        required=True,
        help="Board base URL, for example http://192.168.1.50:8080",
    )
    parser.add_argument(
        "--device",
        default=None,
        help="NI device or module name. Default: first module whose product type contains 9266",
    )
    parser.add_argument(
        "--group",
        default=None,
        help="Run this group id only. Default: every enabled group",
    )
    parser.add_argument("--workbook", type=Path, default=DEFAULT_WORKBOOK)
    parser.add_argument(
        "--settle",
        type=float,
        default=DEFAULT_SETTLE_S,
        help="Seconds to wait after each write before reading /api/live",
    )
    parser.add_argument("--timeout", type=float, default=5.0, help="HTTP timeout seconds")
    parser.add_argument("--output", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument(
        "--trip-close",
        action="store_true",
        help="After analog groups, apply trip then close and check b7 counters +1",
    )
    parser.add_argument(
        "--trip-close-only",
        action="store_true",
        help="Skip analog groups; only run trip/close counter checks",
    )
    parser.add_argument(
        "--trip-device",
        default=None,
        help="Digital module name for trip/close (default: first USB-6509 / DO module)",
    )
    parser.add_argument(
        "--trip-channels",
        type=Path,
        default=None,
        help="JSON map of trip_coil/switch_52a/switch_52b/close_coil to DAQmx paths",
    )
    parser.add_argument(
        "--trip-close-cycles",
        type=int,
        default=1,
        help="How many trip+close pairs to run (default 1)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.settle < 0:
        print("settle must be >= 0", file=sys.stderr)
        return 2
    if args.trip_close_cycles < 1:
        print("trip-close-cycles must be >= 1", file=sys.stderr)
        return 2
    run_breaker = args.trip_close or args.trip_close_only
    rows: list[dict] = []
    try:
        if not args.trip_close_only:
            groups = enabled_groups(args.group)
            steps = load_steps(args.workbook)
            rows.extend(
                run_groups(
                    groups,
                    steps,
                    board=args.board,
                    device=args.device,
                    settle=args.settle,
                    timeout=args.timeout,
                )
            )
        if run_breaker:
            if args.trip_channels is not None:
                channels = load_channels_json(args.trip_channels)
            else:
                channels = default_trip_channels()
            trip_device = args.trip_device or device_name_from_channels(channels or {})
            print(
                f"group trip_close kind=breaker counter check "
                f"cycles={args.trip_close_cycles} device={trip_device or 'auto'}"
            )
            breaker_rows = run_trip_close_test(
                args.board,
                device=trip_device,
                channels=channels,
                settle=args.settle,
                timeout=args.timeout,
                cycles=args.trip_close_cycles,
            )
            for row in breaker_rows:
                _print_result(row)
            rows.extend(breaker_rows)
    except (OSError, RuntimeError, ValueError, LiveError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if not rows:
        print("No tests ran. Enable a group or pass --trip-close / --trip-close-only.", file=sys.stderr)
        return 2

    append_results(args.output, rows)
    passed = sum(1 for row in rows if row["result"] == "PASS")
    failed = len(rows) - passed
    print(f"{passed} pass, {failed} fail, {len(rows)} total -> {args.output}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
