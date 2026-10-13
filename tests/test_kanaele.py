"""Channel entities, white/color mix, group color modes, presence and lux.

Units are built from real fixtures (tests/fixtures/<id>.json from api.casambi.com).
One test per kind of channel, not per fixture id. No Bluetooth needed.
"""

import json
import logging
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

from CasambiBt import Casambi, Group, Unit
from CasambiBt._network import _parseUnitType, _withUnitCctRange
from CasambiBt._unit import UnitControlType
import pytest

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.light import ColorMode, valid_supported_color_modes
from homeassistant.components.sensor import SensorDeviceClass

sys.path.insert(0, str(Path(__file__).parents[1]))

from custom_components.casambi_bt.binary_sensor import CasambiPresenceSensor
from custom_components.casambi_bt.light import (
    CasambiChannelLight,
    CasambiLightGroup,
    CasambiLightUnit,
)
from custom_components.casambi_bt.number import CasambiWhiteColorNumber
from custom_components.casambi_bt.sensor import CasambiLuxSensor

FIXTURES = Path(__file__).parent / "fixtures"
LED8_SETTINGS = {"cct.minKelvins": 2100.0, "cct.maxKelvins": 3500.0}


def make_unit(fixture_id: int, device_id: int = 1, settings: dict | None = None):
    unit_type = _parseUnitType(
        json.loads((FIXTURES / f"{fixture_id}.json").read_text()),
        logging.getLogger(__name__),
    )
    unit_type = _withUnitCctRange(unit_type, settings or {})
    unit = Unit(
        fixture_id, device_id, f"uuid-{device_id}", "addr", "name", "1", unit_type
    )
    unit._online = True
    return unit


@pytest.fixture
def api(tmp_path):
    """Real Casambi object, only the Bluetooth send is replaced."""
    casa = Casambi(cachePath=tmp_path)
    casa._send = AsyncMock()
    casa._casaNetwork = SimpleNamespace(protocolVersion=10)  # Evolution, not Classic
    return SimpleNamespace(casa=casa, available=True)


def sent_state(api) -> bytes:
    payload = api.casa._send.await_args.args[1]
    return bytes(payload)


def channel_lights(api, unit):
    return [
        CasambiChannelLight(api, unit, c)
        for c in unit.unitType.controls
        if c.type == UnitControlType.SLIDER and c.name
    ]


async def test_slider_channels_are_separate_lights(api) -> None:
    """4CH: one dimmable light per named channel, each writes only its own field."""
    unit = make_unit(37068)
    unit.setStateFromBytes(bytes([10, 20, 0, 40]))
    lights = channel_lights(api, unit)

    assert [entity.entity_description.name for entity in lights] == [
        "CH1",
        "CH2",
        "CH3",
        "CH4",
    ]
    assert [entity.brightness for entity in lights] == [10, 20, 0, 40]
    assert [entity.is_on for entity in lights] == [True, True, False, True]
    assert all(e.supported_color_modes == {ColorMode.BRIGHTNESS} for e in lights)
    assert len({e.entity_description.entity_type for e in lights}) == 4

    await lights[2].async_turn_on(brightness=99)
    assert sent_state(api) == bytes([10, 20, 99, 40])
    await lights[0].async_turn_off()
    assert sent_state(api) == bytes([0, 20, 99, 40])
    await lights[3].async_turn_on()
    assert sent_state(api) == bytes([0, 20, 99, 255])


async def test_dimmer_with_slider_channels(api) -> None:
    """Sliders/RGBW: main light for the dimmer, channel lights for Red/Green/Blue/White."""
    unit = make_unit(40271)
    unit.setStateFromBytes(bytes([255, 1, 2, 3, 4]))

    main = CasambiLightUnit(api, unit)
    assert main.supported_color_modes == {ColorMode.BRIGHTNESS}
    await main.async_turn_on(brightness=100)
    # The channels survive a dimmer change.
    assert sent_state(api) == bytes([100, 1, 2, 3, 4])

    names = [e.entity_description.name for e in channel_lights(api, unit)]
    assert names == ["Red", "Green", "Blue", "White"]


async def test_rgb_tw_light_and_mix(api) -> None:
    """RGB/TW: one light with color and temperature in the unit's own range, one mix slider."""
    unit = make_unit(27399, settings=LED8_SETTINGS)
    light = CasambiLightUnit(api, unit)
    assert light.supported_color_modes == {ColorMode.RGB, ColorMode.COLOR_TEMP}
    assert (light.min_color_temp_kelvin, light.max_color_temp_kelvin) == (2100, 3500)

    mix = CasambiWhiteColorNumber(api, unit)
    assert (mix.native_min_value, mix.native_max_value) == (0, 100)

    # Left stop = only white, raw 0 per library doc (measurement against the app pending).
    unit.setStateFromBytes(bytes([255, 0, 0, 0, 255]))
    assert mix.native_value == 0
    assert light.color_mode == ColorMode.COLOR_TEMP

    unit.setStateFromBytes(bytes([255, 0, 0, 63 << 2, 255]))
    assert mix.native_value == 100
    assert light.color_mode == ColorMode.RGB

    for pct, raw in ((0, 0), (50, 32), (100, 63)):
        await mix.async_set_native_value(pct)
        assert sent_state(api)[3] >> 2 == raw


async def test_rgbw_without_temperature(api) -> None:
    """RGBW: color and mix, no temperature, so the light stays in color mode."""
    unit = make_unit(40210)
    unit.setStateFromBytes(bytes([255, 0, 0, 0]))
    light = CasambiLightUnit(api, unit)
    assert light.supported_color_modes == {ColorMode.RGB}
    assert light.color_mode == ColorMode.RGB
    assert CasambiWhiteColorNumber(api, unit).native_value == 0


def test_group_with_4ch_member_has_valid_color_modes(api) -> None:
    """Flur: CBU-TED + TW units + 4CH. The 4CH member must not break the group."""
    group = Group(
        3,
        "Flur",
        [
            make_unit(526, 1),
            make_unit(526, 2),
            make_unit(18285, 3),
            make_unit(37068, 4),
            make_unit(27402, 5),
        ],
    )
    entity = CasambiLightGroup(api, group)
    valid_supported_color_modes(entity.supported_color_modes)
    assert entity.supported_color_modes == {ColorMode.COLOR_TEMP}
    assert entity.color_mode == ColorMode.COLOR_TEMP


def test_group_of_only_slider_units_is_onoff(api) -> None:
    entity = CasambiLightGroup(api, Group(9, "Nur 4CH", [make_unit(37068, 1)]))
    assert entity.supported_color_modes == {ColorMode.ONOFF}


def test_presence_and_lux(api) -> None:
    unit = make_unit(5511)
    presence = CasambiPresenceSensor(api, unit)
    lux = CasambiLuxSensor(api, unit)
    assert presence.device_class == BinarySensorDeviceClass.OCCUPANCY
    assert lux.device_class == SensorDeviceClass.ILLUMINANCE
    assert lux.native_unit_of_measurement == "lx"
    assert presence.is_on is None
    assert lux.native_value is None

    # presence: bits 0-1, lux: bits 2-13
    unit.setStateFromBytes(((345 << 2) | 1).to_bytes(2, "little"))
    assert presence.is_on is True
    assert lux.native_value == 345

    unit.setStateFromBytes((12 << 2).to_bytes(2, "little"))
    assert presence.is_on is False
    assert lux.native_value == 12
