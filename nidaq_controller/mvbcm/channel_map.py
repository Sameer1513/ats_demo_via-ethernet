"""Which NI outputs feed which board signals.

Enable a group by filling its channels and setting ``enabled``. The runner
skips groups that are disabled. Adding the next three analog channels, or
the digital lines, is another group — not another script.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MappedChannel:
    """One stimulus line and the sheet rows it satisfies."""

    ni_channel: str
    signal: str
    sheet_label: str
    test_ids: tuple[str, ...]
    input_unit: str = ""
    scale: float = 1.0
    sweep: str = ""


@dataclass(frozen=True)
class ChannelGroup:
    """Channels that share one output task.

    ``kind`` is ``current``, ``voltage``, or ``digital``. An empty kind
    means the group is a placeholder.
    ``device`` is a DAQmx device name. ``None`` asks the runner to pick
    the first NI-9266 when the kind is current.
    """

    id: str
    kind: str
    enabled: bool
    device: str | None
    channels: tuple[MappedChannel, ...]


# Live signals are tc1, tc2, cc, imtr (see live-api.md).
# NI-9266 terminals 0, 1, 2, 4 are ao0, ao1, ao2, ao4.
# imtr is the motor 4-20 mA input (AI-02 steps in mA). The mV rows
# on that same test are a different stimulus and are not driven here.
# Sweeps replace the coarse sheet points. Limits stay the sheet full-scale
# window: ±0.5% of 50 A on the coils, ±1% of 100 A on imtr, ±1% of 20 A on iph.
_TC_CURRENT = (
    MappedChannel("ao0", "tc1", "TC1", ("AI-01",), sweep="coil_ma"),
    MappedChannel("ao1", "tc2", "TC2", ("AI-01",), sweep="coil_ma"),
    MappedChannel("ao2", "cc", "CC", ("AI-01",), sweep="coil_ma"),
    MappedChannel("ao4", "imtr", "Motor", ("AI-02",), "mA", sweep="imtr_ma"),
)

GROUPS: tuple[ChannelGroup, ...] = (
    ChannelGroup(
        id="tc_current",
        kind="current",
        enabled=True,
        device=None,
        channels=_TC_CURRENT,
    ),
    ChannelGroup(
        id="iph_voltage",
        kind="voltage",
        enabled=True,
        device=None,
        channels=(
            # 9264 ao0 is terminal 0. The fixture divides by 11, so the
            # card is driven at 11 times the sheet millivolts.
            MappedChannel("ao0", "iph", "Load-current", ("AI-03",), "mV RMS", 11.0, "iph_pct"),
        ),
    ),
    # Next analog channels. Set kind, fill channels, then set enabled.
    ChannelGroup(
        id="analog_b",
        kind="",
        enabled=False,
        device=None,
        channels=(),
    ),
    # Digital steps. Fill with DO lines, then set enabled.
    # Drive them one line at a time; they are not part of the AO task.
    ChannelGroup(
        id="digital",
        kind="digital",
        enabled=False,
        device=None,
        channels=(),
    ),
)


def enabled_groups(only: str | None = None) -> list[ChannelGroup]:
    """Groups to run.

    ``only`` selects one group id. A disabled group stays disabled.
    """
    if only:
        match = [group for group in GROUPS if group.id == only]
        if not match:
            known = ", ".join(group.id for group in GROUPS)
            raise ValueError(f"Unknown group {only!r}. Known groups: {known}")
        group = match[0]
        if not group.enabled:
            raise ValueError(f"Group {group.id!r} is disabled")
        return [group]
    return [group for group in GROUPS if group.enabled]
