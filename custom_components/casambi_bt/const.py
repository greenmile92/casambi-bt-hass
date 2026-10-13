"""Constants for the Casambi Bluetooth integration."""

from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "casambi_bt"

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.EVENT,
    Platform.LIGHT,
    Platform.SCENE,
    Platform.NUMBER,
    Platform.SENSOR,
]

CONF_IMPORT_GROUPS: Final = "import_groups"

RECONNECT_BACKOFF_START: Final = 2
RECONNECT_BACKOFF_STEP: Final = 2
RECONNECT_BACKOFF_MAX: Final = 300

SWITCH_EVENT: Final = f"{DOMAIN}_switch_event"

# ponytail: detected by unit type mode, the library maps the pushButtonState control to UNIMPLEMENTED.
SWITCH_MODES: Final = ("Kinetic Switch",)
# ponytail: fixed, the library does not expose the button list of a fixture. PTM215B has 4.
SWITCH_BUTTONS: Final = 4
