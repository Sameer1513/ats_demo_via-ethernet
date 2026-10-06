"""Output drivers. Each group kind has one.

Current is implemented. Voltage and digital share the same write / idle /
close shape and raise until those groups are enabled.
"""

from nidaq_controller.mvbcm.stimuli.current import CurrentStimulus, find_9266
from nidaq_controller.mvbcm.stimuli.digital import DigitalStimulus
from nidaq_controller.mvbcm.stimuli.voltage import VoltageStimulus

__all__ = [
    "CurrentStimulus",
    "DigitalStimulus",
    "VoltageStimulus",
    "find_9266",
]
