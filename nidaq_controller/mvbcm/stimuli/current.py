"""DC current on an NI-9266.

One task holds every channel in the group. The channel list comes from
the map, so a later trio on the same module is more entries, not a new task
shape. Values passed to :meth:`write` are milliamps. DAQmx receives amps.
"""

from __future__ import annotations

from nidaq_controller.constants import DEFAULT_CURRENT_RANGE, MILLIAMPS_TO_AMPS
from nidaq_controller.mvbcm.channel_map import MappedChannel


class CurrentStimulus:
    """Software-timed current output. The level holds after each write."""

    def __init__(self, device_name: str, channels: tuple[MappedChannel, ...] | list[MappedChannel]):
        if not channels:
            raise ValueError("current group has no channels")
        if not device_name:
            raise ValueError("current group has no device name")
        import nidaqmx

        self._signals = [channel.signal for channel in channels]
        physical = [
            channel.ni_channel
            if "/" in channel.ni_channel
            else f"{device_name}/{channel.ni_channel}"
            for channel in channels
        ]
        low, high = DEFAULT_CURRENT_RANGE
        self._task = nidaqmx.Task()
        self._started = False
        try:
            self._task.ao_channels.add_ao_current_chan(
                ", ".join(physical),
                min_val=low,
                max_val=high,
            )
        except Exception as exc:
            self._task.close()
            raise RuntimeError(
                f"Could not open current outputs on {device_name}: {exc}. "
                "Stop any analog output already running on this module."
            ) from exc

    def write(self, milliamps_by_signal: dict[str, float], frequency: float = 50.0) -> None:
        """Set each channel. Missing signals are written as 0 mA."""
        amps = []
        low, high = DEFAULT_CURRENT_RANGE
        for signal in self._signals:
            milliamps = float(milliamps_by_signal.get(signal, 0.0))
            value = milliamps / MILLIAMPS_TO_AMPS
            if value < low or value > high:
                raise ValueError(
                    f"{signal} {milliamps} mA is outside "
                    f"[{low * MILLIAMPS_TO_AMPS:g}, {high * MILLIAMPS_TO_AMPS:g}] mA"
                )
            amps.append(value)
        # On-demand AO writes one sample and then the task is no longer
        # running, so the next write must auto-start again. If a device
        # leaves the task running, fall back to a plain write.
        try:
            self._task.write(amps, auto_start=True)
        except Exception as exc:
            message = str(exc).lower()
            if "running" not in message and "-200479" not in message:
                raise
            self._task.write(amps, auto_start=False)
        self._started = True

    def idle(self) -> None:
        """Drive every channel in the task to 0 mA."""
        self.write({})

    def close(self) -> None:
        """Zero the outputs and release the module."""
        task = getattr(self, "_task", None)
        if task is None:
            return
        try:
            try:
                self.idle()
            except Exception:
                pass
            if self._started:
                try:
                    task.stop()
                except Exception:
                    pass
        finally:
            task.close()
            self._task = None
            self._started = False


def find_9266() -> str:
    """Name of the first module whose product type contains ``9266``."""
    from nidaq_controller.core.device_manager import DeviceManager

    manager = DeviceManager()
    found: list[str] = []
    for device in manager.discover_devices():
        modules = device.modules or [device]
        for module in modules:
            product = (getattr(module, "product_type", "") or "").upper()
            if "9266" in product:
                found.append(module.name)
    if not found:
        raise RuntimeError("No NI-9266 module found. Pass --device.")
    return found[0]
