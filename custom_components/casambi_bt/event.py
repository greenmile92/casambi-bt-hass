"""Event entities for the buttons of Casambi switches."""

import logging
from typing import cast

from CasambiBt import Unit as CasambiUnit
from CasambiBt._switch import SwitchEvent

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DOMAIN, CasambiApi
from .const import SWITCH_BUTTONS, SWITCH_MODES
from .entities import CasambiUnitEntity, TypedEntityDescription

_LOGGER = logging.getLogger(__name__)

EVENT_TYPES = ["press", "release", "hold", "release_after_hold"]


def is_switch(unit: CasambiUnit) -> bool:
    """Return True if the unit is a switch that sends button events."""
    return unit.unitType.mode in SWITCH_MODES


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one event entity per button of every switch."""
    api: CasambiApi = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities(
        CasambiButtonEvent(api, unit, button)
        for unit in api.get_units()
        if is_switch(unit)
        for button in range(1, SWITCH_BUTTONS + 1)
    )


class CasambiButtonEvent(CasambiUnitEntity, EventEntity):
    """Event entity for one button of a Casambi switch."""

    _attr_device_class = EventDeviceClass.BUTTON
    _attr_event_types = EVENT_TYPES

    def __init__(self, api: CasambiApi, unit: CasambiUnit, button: int) -> None:
        """Initialize the button event entity."""
        super().__init__(
            api,
            TypedEntityDescription(
                key=f"button_{button}",
                name=f"Button {button}",
                entity_type=f"button-{button}",
            ),
            unit,
        )
        self._button = button

    @property
    def available(self) -> bool:
        """Switches are battery-less radio senders, only the network connection counts."""
        return self._api.available

    @callback
    def _switch_callback(self, event: SwitchEvent) -> None:
        if event.button != self._button:
            return
        self._trigger_event(event.event.name.lower())
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Register for switch events."""
        await super().async_added_to_hass()
        unit = cast("CasambiUnit", self._obj)
        self._api.register_switch_updates(unit, self._switch_callback)
        self._api.register_connection_updates(self.async_write_ha_state)

    async def async_will_remove_from_hass(self) -> None:
        """Unregister from switch events."""
        await super().async_will_remove_from_hass()
        unit = cast("CasambiUnit", self._obj)
        self._api.unregister_switch_updates(unit, self._switch_callback)
        self._api.unregister_connection_updates(self.async_write_ha_state)
