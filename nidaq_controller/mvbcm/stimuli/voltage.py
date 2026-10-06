"""Voltage analog output.

Same write / idle / close shape as the current driver. Not used while the
voltage group is disabled. A voltage module is its own task; its channels
are not added to the 9266 current task.
"""

from __future__ import annotations


class VoltageStimulus:
    def __init__(self, device_name: str, channels) -> None:
        raise RuntimeError("voltage stimulus is not enabled")

    def write(self, values: dict[str, float]) -> None:
        raise RuntimeError("voltage stimulus is not enabled")

    def idle(self) -> None:
        raise RuntimeError("voltage stimulus is not enabled")

    def close(self) -> None:
        return None
