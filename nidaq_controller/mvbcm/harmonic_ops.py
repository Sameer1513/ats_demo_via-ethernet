"""Odd-harmonic RMS check for iph (analog) and imtr (CT motor).

One runner, two targets. Per step: drive H1, read live ``peak_eng``, add
every selected odd Hn at that percent of the web peak. Score ``rms_eng`` at
``expect × k ± tol`` where ``k = sqrt(1 + Σ (Hn%/100)²)``.

iph: 0–20 A ±0.2 A. imtr: 0–100 A ±1 A. Hn skipped at 0 V.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from nidaq_controller.mvbcm.breaker_ops import (
    MvbcmStopped,
    _raise_if_stopped,
    _sleep_interruptible,
)
from nidaq_controller.mvbcm.channel_map import MappedChannel
from nidaq_controller.mvbcm.live import LiveError, fetch_live, in_limits, measure
from nidaq_controller.mvbcm.stimuli.voltage import VoltageStimulus, find_9264

H3_PCT = 10.0
HARMONIC_SETTLE_S = 3.0
HARMONIC_STEP_GAP_S = 5.0
HARMONIC_FREQUENCY_HZ = 50.0
ODD_ORDERS = (3, 5, 7, 9, 11, 13, 15, 17, 19, 21)

_IPH_CHANNEL = MappedChannel(
    "ao0", "iph", "Load-current", ("AI-03",), "mV RMS", 11.0, "iph_pct",
)
_IMTR_CHANNEL = MappedChannel("ao0", "imtr", "Motor", ("AI-02",), "V")


@dataclass(frozen=True)
class HarmonicTarget:
    id: str
    signal: str
    channel: MappedChannel
    test_id: str
    group: str
    fs_a: float
    tol_a: float
    fs_card_vrms: float
    steps: tuple[dict[str, Any], ...]


def _pct_steps(*, fs_a: float, h1_peak_at_fs: float, stimulus_at_fs: float) -> tuple[dict[str, Any], ...]:
    steps = []
    for percent in range(0, 101, 10):
        frac = percent / 100.0
        steps.append({
            "step": f"{percent}pct",
            "percent": percent,
            "h1_peak": h1_peak_at_fs * frac,
            "stimulus_value": stimulus_at_fs * frac,
            "expect_a": fs_a * frac,
        })
    return tuple(steps)


_IPH_FS_MV = 333.0
_IPH_H1_PEAK = (_IPH_FS_MV / 1000.0) * 11.0 * math.sqrt(2.0)
_IMTR_FS_VRMS = 3.663
_IMTR_H1_PEAK = _IMTR_FS_VRMS * math.sqrt(2.0)

TARGETS: dict[str, HarmonicTarget] = {
    "iph": HarmonicTarget(
        id="iph",
        signal="iph",
        channel=_IPH_CHANNEL,
        test_id="AI-03-H",
        group="harmonic_iph",
        fs_a=20.0,
        tol_a=0.2,
        fs_card_vrms=_IPH_FS_MV / 1000.0 * 11.0,
        steps=_pct_steps(
            fs_a=20.0, h1_peak_at_fs=_IPH_H1_PEAK, stimulus_at_fs=_IPH_FS_MV,
        ),
    ),
    "imtr": HarmonicTarget(
        id="imtr",
        signal="imtr",
        channel=_IMTR_CHANNEL,
        test_id="AI-02-H",
        group="harmonic_imtr",
        fs_a=100.0,
        tol_a=1.0,
        fs_card_vrms=_IMTR_FS_VRMS,
        steps=_pct_steps(
            fs_a=100.0, h1_peak_at_fs=_IMTR_H1_PEAK, stimulus_at_fs=_IMTR_FS_VRMS,
        ),
    ),
}

IPH_HARMONIC_STEPS = TARGETS["iph"].steps


def rms_scale(harmonics: dict[int, float]) -> float:
    """True-RMS gain vs H1-only: sqrt(1 + Σ (pct/100)²)."""
    acc = 0.0
    for pct in harmonics.values():
        frac = float(pct) / 100.0
        if frac > 0:
            acc += frac * frac
    return math.sqrt(1.0 + acc)


def normalize_harmonics(
    raw, *, fallback_h3_pct: float = H3_PCT, require: bool = True,
) -> dict[int, float]:
    """Selected odd orders → pct. ``raw`` is ``[{order, pct}, ...]``."""
    selected: dict[int, float] = {}
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                raise ValueError("harmonics entries must be objects")
            try:
                order = int(item.get("order"))
                pct = float(item.get("pct"))
            except (TypeError, ValueError) as exc:
                raise ValueError("harmonics order/pct invalid") from exc
            if order not in ODD_ORDERS:
                raise ValueError(f"harmonic order {order} must be odd H3–H21")
            if not (0.0 <= pct <= 100.0):
                raise ValueError(f"H{order} % must be 0–100")
            if pct > 0.0:
                selected[order] = pct
    elif raw is None and require:
        pct = float(fallback_h3_pct)
        if not (0.0 <= pct <= 100.0):
            raise ValueError("harmonic_h3_pct must be 0–100")
        if pct > 0.0:
            selected[3] = pct
    if require and not selected:
        raise ValueError("Select at least one odd harmonic H3–H21 with % > 0")
    return dict(sorted(selected.items()))


def _read_settled(fetch, board: str, settle: float, should_stop=None):
    from nidaq_controller.mvbcm.run_mvbcm import _read_settled as _impl
    return _impl(fetch, board, settle, should_stop=should_stop)


def run_iph_harmonic_test(board: str, **kwargs) -> list[dict]:
    kwargs.pop("targets", None)
    return run_harmonic_test(board, targets=("iph",), **kwargs)


def run_imtr_harmonic_test(board: str, **kwargs) -> list[dict]:
    kwargs.pop("targets", None)
    return run_harmonic_test(board, targets=("imtr",), **kwargs)


def run_harmonic_test(
    board: str,
    *,
    targets: tuple[str, ...] | list[str] = ("iph",),
    device: str | None = None,
    settle: float = HARMONIC_SETTLE_S,
    timeout: float = 5.0,
    h3_pct: float = H3_PCT,
    harmonics: dict[int, float] | None = None,
    fetch=None,
    on_row=None,
    should_stop=None,
) -> list[dict]:
    """Drive H1, read web peak_eng, add selected Hn; score ``rms_eng`` per target."""
    if harmonics is None:
        harmonics = normalize_harmonics(None, fallback_h3_pct=h3_pct)
    else:
        harmonics = normalize_harmonics(
            [{"order": order, "pct": pct} for order, pct in harmonics.items()],
            fallback_h3_pct=h3_pct,
        )
    if fetch is None:
        def fetch(url, _timeout=timeout):
            return fetch_live(url, timeout=_timeout)

    device_name = device or find_9264()
    rows: list[dict] = []
    for name in targets:
        try:
            target = TARGETS[name]
        except KeyError as exc:
            known = ", ".join(TARGETS)
            raise ValueError(f"unknown harmonic target {name!r}. Known: {known}") from exc
        rows.extend(
            _run_target(
                board, fetch, device_name, settle, target, harmonics,
                on_row=on_row, should_stop=should_stop,
            )
        )
    return rows


def _run_target(
    board, fetch, device_name, settle, target: HarmonicTarget, harmonics,
    *, on_row, should_stop,
) -> list[dict]:
    rows: list[dict] = []
    stimulus: VoltageStimulus | None = None
    try:
        for index, spec in enumerate(target.steps):
            _raise_if_stopped(should_stop)
            if index > 0:
                if stimulus is not None:
                    try:
                        stimulus.idle()
                    except Exception:
                        pass
                    try:
                        stimulus.close()
                    except Exception:
                        pass
                    stimulus = None
                _sleep_interruptible(HARMONIC_STEP_GAP_S, should_stop)
            stimulus = VoltageStimulus(device_name, [target.channel])
            row = _run_one(
                board, fetch, stimulus, settle, target, spec, harmonics,
                should_stop=should_stop,
            )
            rows.append(row)
            if on_row is not None:
                on_row(row)
    except MvbcmStopped:
        raise
    finally:
        if stimulus is not None:
            try:
                stimulus.close()
            except Exception:
                pass
    return rows


def _peak_eng_to_card_v(target: HarmonicTarget, peak_eng: float) -> float:
    """Live PEAK (ENG.) amps → 9264 ao0 volts peak."""
    return float(peak_eng) * target.fs_card_vrms / target.fs_a


def _hn_label(harmonics: dict[int, float]) -> str:
    return "+".join(f"H{n} {pct:g}%" for n, pct in harmonics.items())


def _run_one(
    board, fetch, stimulus, settle, target: HarmonicTarget, spec, harmonics,
    *, should_stop=None,
) -> dict:
    h1_peak = float(spec["h1_peak"])
    stimulus_value = float(spec["stimulus_value"])
    h1_expect = float(spec["expect_a"])
    k = rms_scale(harmonics)
    expect = h1_expect * k
    low = expect - target.tol_a
    high = expect + target.tol_a
    step = str(spec["step"])
    signal = target.signal
    try:
        stimulus.write_peak_volts({signal: h1_peak}, frequency=HARMONIC_FREQUENCY_HZ)
    except Exception as exc:
        return _row(
            target, step, stimulus_value, expect, low, high, None, "FAIL",
            f"AO H1 write failed: {exc}",
        )

    if h1_peak == 0.0:
        expect = h1_expect
        low = expect - target.tol_a
        high = expect + target.tol_a
        try:
            payload = _read_settled(fetch, board, settle, should_stop=should_stop)
            measured = measure(payload, signal, "rms_eng")
        except LiveError as exc:
            return _row(
                target, step, stimulus_value, expect, low, high, None, "FAIL",
                str(exc),
            )
        except MvbcmStopped:
            raise
        ok = in_limits(measured, low, high)
        note = f"H1 0; harmonics skipped ({signal})"
        if not ok:
            note = f"expected [{low}, {high}] A RMS, got {measured}; {note}"
        return _row(
            target, step, stimulus_value, expect, low, high, measured,
            "PASS" if ok else "FAIL", note,
        )

    try:
        payload = _read_settled(fetch, board, settle, should_stop=should_stop)
        peak_eng = measure(payload, signal, "peak_eng")
    except LiveError as exc:
        return _row(
            target, step, stimulus_value, expect, low, high, None, "FAIL",
            str(exc),
        )
    except MvbcmStopped:
        raise

    card_from_web = _peak_eng_to_card_v(target, peak_eng)
    overtones = {
        order: {signal: card_from_web * (float(pct) / 100.0)}
        for order, pct in harmonics.items()
    }
    try:
        stimulus.write_harmonics_card(
            {signal: h1_peak}, overtones, frequency=HARMONIC_FREQUENCY_HZ,
        )
    except Exception as exc:
        return _row(
            target, step, stimulus_value, expect, low, high, None, "FAIL",
            f"AO harmonic write failed (web peak_eng={peak_eng:.3f} A): {exc}",
        )
    try:
        payload = _read_settled(fetch, board, settle, should_stop=should_stop)
        measured = measure(payload, signal, "rms_eng")
    except LiveError as exc:
        return _row(
            target, step, stimulus_value, expect, low, high, None, "FAIL",
            str(exc),
        )
    except MvbcmStopped:
        raise

    ok = in_limits(measured, low, high)
    note = (
        f"{signal} web peak_eng={peak_eng:.3f} A; "
        f"{_hn_label(harmonics)}; k={k:.4f} ±{target.tol_a:g} A"
    )
    if not ok:
        note = f"expected [{low}, {high}] A RMS, got {measured}; {note}"
    return _row(
        target, step, stimulus_value, expect, low, high, measured,
        "PASS" if ok else "FAIL", note,
    )


def _row(
    target: HarmonicTarget,
    step: str,
    stimulus_value: float,
    expect: float,
    low: float,
    high: float,
    measured: float | None,
    result: str,
    note: str,
) -> dict:
    return {
        "group": target.group,
        "test_id": target.test_id,
        "step": step,
        "signal": target.signal,
        "phase": "harmonic",
        "stimulus_value": f"{stimulus_value:g}",
        "unit": "A RMS",
        "measured": "" if measured is None else f"{measured:.3f}",
        "field": "rms_eng",
        "min": f"{low:.3f}",
        "max": f"{high:.3f}",
        "result": result,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "note": note or f"expect={expect:g} A RMS",
    }
