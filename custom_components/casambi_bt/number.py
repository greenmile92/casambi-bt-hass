"""Number entities: vertical control and white/color balance of Casambi lights."""

from abc import ABCMeta
import logging
from typing import Final, cast

from CasambiBt import Group, Unit, UnitControlType, UnitState

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import CasambiApi
from .const import CONF_IMPORT_GROUPS, DOMAIN
from .entities import (
    CasambiEntity,
    CasambiNetworkGroup,
    CasambiUnitEntity,
    TypedEntityDescription,
)

_LOGGER = logging.getLogger(__name__)

# ponytail: direction from the library doc (raw 0 = only white, 63 = only color),
# not yet measured against the app. If the app shows it the other way round, flip this.
WHITE_ONLY_AT_ZERO: Final = True


def mix_from_raw(raw: int) -> float:
    """Position of the white/color mix: 0 = only white, 50 = both full, 100 = only color."""
    pct = raw * 100 / UnitState.WCB_MAX
    return pct if WHITE_ONLY_AT_ZERO else 100 - pct


def raw_from_mix(pct: float) -> int:
    """Inverse of :func:`mix_from_raw`."""
    if not WHITE_ONLY_AT_ZERO:
        pct = 100 - pct
    return round(pct * UnitState.WCB_MAX / 100)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the Casambi vertical entity."""
    casa_api: CasambiApi = hass.data[DOMAIN][config_entry.entry_id]

    light_entities: list[CasambiVerticalNumber] = [
        CasambiVerticalNumberUnit(casa_api, u)
        for u in casa_api.get_units([UnitControlType.VERTICAL])
    ]

    group_entities: list[CasambiVerticalNumber] = []
    if config_entry.data[CONF_IMPORT_GROUPS]:
        for g in casa_api.get_groups():
            has_vert = False
            for u in g.units:
                if u.unitType.get_control(UnitControlType.VERTICAL) is not None:
                    has_vert = True
                    break
            if has_vert:
                group_entities.append(CasambiVerticalNumberGroup(casa_api, g))

    balance_entities = [
        CasambiWhiteColorNumber(casa_api, u)
        for u in casa_api.get_units([UnitControlType.WHITECOLORBALANCE])
    ]

    async_add_entities(light_entities + group_entities + balance_entities)


class TypedNumberEntityDescription(TypedEntityDescription, NumberEntityDescription):
    """Describes a CasambiVerticalNumberUnit."""


class CasambiVerticalNumber(CasambiEntity, NumberEntity, metaclass=ABCMeta):
    """Defines a Casambi vertical entity base class.

    This class contains common functionality for units and groups.
    """

    def __init__(
        self,
        api: CasambiApi,
        description: TypedNumberEntityDescription,
        obj: Group | Unit,
    ) -> None:
        """Initialize a Casambi vertical entity base class."""

        self._attr_device_class = NumberDeviceClass.ILLUMINANCE
        self._attr_native_min_value = 0
        self._attr_native_max_value = 255

        self._obj: Group | Unit
        super().__init__(api, description, obj)

    async def async_set_native_value(self, value: float) -> None:
        """Set the vertical value."""
        await self._api.casa.setVertical(self._obj, int(value))


class CasambiVerticalNumberUnit(CasambiVerticalNumber, CasambiUnitEntity):
    """Defines a Casambi vertical entity."""

    def __init__(self, api: CasambiApi, unit: Unit) -> None:
        """Initialize a Casambi vertical entity."""

        desc = TypedNumberEntityDescription(key=unit.uuid, entity_type="vertical")

        self._obj: Unit
        super().__init__(api, desc, unit)

    @property
    def native_value(self) -> float | None:
        """Get the vertical value of the unit."""
        unit = cast("Unit", self._obj)
        if unit.state is not None and unit.state.vertical is not None:
            return float(unit.state.vertical)
        return None


class CasambiVerticalNumberGroup(CasambiVerticalNumber, CasambiNetworkGroup):
    """Defines a Casambi vertical entity group."""

    def __init__(self, api: CasambiApi, group: Group) -> None:
        """Initialize a Casambi vertical group entity."""

        desc = TypedNumberEntityDescription(
            key=str(group.groudId), name=group.name, entity_type="vertical"
        )

        self._obj: Group
        super().__init__(api, desc, group)

    @property
    def native_value(self) -> float | None:
        """Get the average vertical value of the group."""
        group = cast("Group", self._obj)
        values = [
            float(unit.state.vertical)
            for unit in group.units
            if unit.state is not None and unit.state.vertical is not None
        ]
        if values:
            return sum(values) / len(values)
        return None


class CasambiWhiteColorNumber(CasambiUnitEntity, NumberEntity):
    """The white/color mix of an RGB/TW or RGBW unit as one slider, like in the app."""

    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER

    def __init__(self, api: CasambiApi, unit: Unit) -> None:
        """Initialize the white/color balance entity."""
        desc = TypedNumberEntityDescription(
            key=f"{unit.uuid}-white_color_balance",
            translation_key="white_color_balance",
            entity_type="white_color_balance",
        )
        self._obj: Unit
        super().__init__(api, desc, unit)

    @property
    def native_value(self) -> float | None:
        """Return the mix position in percent."""
        unit = cast("Unit", self._obj)
        if unit.state is None or unit.state.white_balance is None:
            return None
        return round(mix_from_raw(unit.state.white_balance))

    async def async_set_native_value(self, value: float) -> None:
        """Set the mix position."""
        await self._api.casa.setWhiteColorBalance(
            cast("Unit", self._obj), raw_from_mix(value)
        )
