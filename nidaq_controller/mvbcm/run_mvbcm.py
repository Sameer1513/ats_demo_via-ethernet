"""Run enabled MV BCM channel groups against the board live API.

Examples::

    .\\.venv\\Scripts\\python.exe -m nidaq_controller.mvbcm.run_mvbcm --board http://192.168.x.x:8080
    .\\.venv\\Scripts\\python.exe -m nidaq_controller.mvbcm.run_mvbcm --board http://192.168.x.x:8080 --device cDAQ1Mod2
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime
from pathlib import Path

from nidaq_controller.mvbcm.channel_map import ChannelGroup, MappedChannel, enabled_groups
from nidaq_controller.mvbcm.live import LiveError, fetch_live, field_for_unit, in_limits, measure
from nidaq_controller.mvbcm.sheet import DEFAULT_WORKBOOK, Step, load_steps, objective_has_label
from nidaq_controller.mvbcm.stimuli.current import CurrentStimulus, find_9266
from nidaq_controller.mvbcm.stimuli.digital import DigitalStimulus
from nidaq_controller.mvbcm.stimuli.voltage import VoltageStimulus

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


def steps_for_channel(steps: list[Step], channel: MappedChannel) -> list[Step]:
    """Sheet rows this channel is responsible for."""
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
                stimulus.write(values)
            except Exception as exc:
                _record(rows, _row(group, channel, step, "individual", None, "", "FAIL", str(exc)), on_row)
                continue
            time.sleep(settle)
            _record(rows, _score(group, channel, step, "individual", board, fetch), on_row)
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
            stimulus.write(values)
        except Exception as exc:
            for channel, step in ready:
                _record(rows, _row(group, channel, step, "simultaneous", None, "", "FAIL", str(exc)), on_row)
            continue
        time.sleep(settle)
        try:
            payload = fetch(board)
        except LiveError as exc:
            for channel, step in ready:
                _record(rows, _row(group, channel, step, "simultaneous", None, "", "FAIL", str(exc)), on_row)
            continue
        for channel, step in ready:
            _record(rows, _score_payload(group, channel, step, "simultaneous", payload), on_row)
    return rows


def _score(group, channel, step, phase, board, fetch) -> dict:
    try:
        payload = fetch(board)
    except LiveError as exc:
        return _row(group, channel, step, phase, None, "", "FAIL", str(exc))
    return _score_payload(group, channel, step, phase, payload)


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
    return ""


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
    parser.add_argument("--settle", type=float, default=1.0, help="Seconds to wait after each write")
    parser.add_argument("--timeout", type=float, default=5.0, help="HTTP timeout seconds")
    parser.add_argument("--output", type=Path, default=DEFAULT_RESULTS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.settle < 0:
        print("settle must be >= 0", file=sys.stderr)
        return 2
    try:
        groups = enabled_groups(args.group)
        steps = load_steps(args.workbook)
        rows = run_groups(
            groups,
            steps,
            board=args.board,
            device=args.device,
            settle=args.settle,
            timeout=args.timeout,
        )
    except (OSError, RuntimeError, ValueError, LiveError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    append_results(args.output, rows)
    passed = sum(1 for row in rows if row["result"] == "PASS")
    failed = len(rows) - passed
    print(f"{passed} pass, {failed} fail, {len(rows)} total -> {args.output}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
