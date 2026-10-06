"""Digital output.

Same write / idle / close shape as the analog drivers. Not used while the
digital group is disabled.

When enabled, drive one line at a time through
``DigitalIOController.set_output_line``. Digital lines are not part of the
analog output task.
"""

from __future__ import annotations


class DigitalStimulus:
    def __init__(self, device_name: str, channels) -> None:
        raise RuntimeError("digital stimulus is not enabled")

    def write(self, values: dict[str, float]) -> None:
        raise RuntimeError("digital stimulus is not enabled")

    def idle(self) -> None:
        raise RuntimeError("digital stimulus is not enabled")

    def close(self) -> None:
        return None
