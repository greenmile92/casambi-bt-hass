"""Sensor entities for Casambi units."""

from typing import cast

from CasambiBt import Unit, UnitControlType

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import LIGHT_LUX
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DOMAIN, CasambiApi
from .entities import CasambiUnitEntity, TypedEntityDescription


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one illuminance sensor per unit with a lux control."""
    api: CasambiApi = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities(
        CasambiLuxSensor(api, unit) for unit in api.get_units([UnitControlType.LUX])
    )


class CasambiLuxSensor(CasambiUnitEntity, SensorEntity):
    """Illuminance of a Casambi sensor (e.g. CBU-CEFL)."""

    _attr_device_class = SensorDeviceClass.ILLUMINANCE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = LIGHT_LUX

    def __init__(self, api: CasambiApi, unit: Unit) -> None:
        """Initialize the illuminance sensor."""
        super().__init__(
            api,
            TypedEntityDescription(key=f"{unit.uuid}-lux", entity_type="lux"),
            unit,
        )

    @property
    def native_value(self) -> int | None:
        """Return the illuminance in lux."""
        unit = cast("Unit", self._obj)
        if unit.state is None:
            return None
        return unit.state.lux
