"""Tests for reconnect (#170) and switch events. No Bluetooth needed.

Run: .venv/bin/python -m pytest tests
"""

import asyncio
import logging
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from CasambiBt import Casambi
from CasambiBt._client import CasambiClientEvolution, ConnectionState
from CasambiBt._switch import ButtonEventType, SwitchEvent
import pytest

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

sys.path.insert(0, str(Path(__file__).parents[1]))

from custom_components.casambi_bt import CasambiApi
from custom_components.casambi_bt.binary_sensor import CasambiBinarySensorEntity
from custom_components.casambi_bt.const import SWITCH_EVENT
from custom_components.casambi_bt.event import CasambiButtonEvent, is_switch

ADDRESS = "AA:BB:CC:DD:EE:FF"


class FakeEntry:
    """Uses the real ConfigEntry.async_create_background_task (eager by default)."""

    def __init__(self) -> None:
        self._background_tasks: set = set()
        self.entry_id = "test"

    async_create_background_task = ConfigEntry.async_create_background_task


@pytest.fixture
async def hass(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    yield hass
    await asyncio.sleep(0)


def make_api(hass, tmp_path) -> tuple[CasambiApi, CasambiClientEvolution, list]:
    """CasambiApi with a real Casambi object and a real client in AUTHENTICATED state."""
    casa = Casambi(cachePath=tmp_path)
    casa._casaNetwork = SimpleNamespace(_networkRevision=1, units=[])

    client = CasambiClientEvolution.__new__(CasambiClientEvolution)
    client._logger = logging.getLogger("CasambiBt._client")
    client.address = client._address_or_devive = ADDRESS
    client._disconnectedCallback = casa._disconnectCallback
    client._connectionState = ConnectionState.AUTHENTICATED
    casa._casaClient = client

    attempts: list[str] = []

    async def fake_disconnect():
        client._connectionState = ConnectionState.NONE

    async def fake_connect_client(_addr):
        attempts.append("connect")
        client._connectionState = ConnectionState.AUTHENTICATED

    client.disconnect = fake_disconnect
    casa._connectClient = fake_connect_client

    api = CasambiApi.__new__(CasambiApi)
    api.hass = hass
    api.conf_entry = FakeEntry()
    api.address = ADDRESS
    api._casa = casa
    api.casa = casa
    api._callback_map = {}
    api._switch_callback_map = {}
    api._connection_callbacks = []
    api._cancel_bluetooth_callback = None
    api._reconnect_task = None
    casa.registerDisconnectCallback(api._casa_disconnect)
    casa.registerSwitchEventHandler(api._switch_event_handler)
    casa.registerUnitChangedHandler(api._unit_changed_handler)
    return api, client, attempts


async def test_reconnect_after_disconnect(hass, tmp_path) -> None:
    """#170: a dropped link must lead to a reconnect attempt, and the status sensor must follow."""
    api, client, attempts = make_api(hass, tmp_path)
    status = CasambiBinarySensorEntity.__new__(CasambiBinarySensorEntity)
    status._api = api
    states: list[bool] = []
    status.async_write_ha_state = lambda: states.append(status.is_on)
    await status.async_added_to_hass()

    with patch(
        "custom_components.casambi_bt.bluetooth.async_ble_device_from_address",
        return_value=ADDRESS,
    ):
        client._on_disconnect(None)  # what bleak calls when the link drops
        await asyncio.sleep(0.05)

    assert attempts == ["connect"]
    assert api.available
    # Disconnect written as off, reconnect written as on.
    assert states == [False, True]


def switch_event(unit_id=7, button=2, event=ButtonEventType.PRESS) -> SwitchEvent:
    return SwitchEvent(button - 1, button, unit_id, 0x06, event, 0, b"")


def make_button(api, button, unit_id=7) -> tuple[CasambiButtonEvent, list]:
    unit = SimpleNamespace(
        deviceId=unit_id,
        uuid="u",
        unitType=SimpleNamespace(mode="Kinetic Switch"),
    )
    entity = CasambiButtonEvent(api, unit, button)
    fired: list[str] = []
    entity._trigger_event = fired.append
    entity.async_write_ha_state = MagicMock()
    api.register_switch_updates(unit, entity._switch_callback)
    return entity, fired


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        (ButtonEventType.PRESS, "press"),
        (ButtonEventType.RELEASE, "release"),
        (ButtonEventType.HOLD, "hold"),
        (ButtonEventType.RELEASE_AFTER_HOLD, "release_after_hold"),
    ],
)
async def test_event_mapping(hass, tmp_path, event, expected) -> None:
    api, _client, _ = make_api(hass, tmp_path)
    _b1, fired1 = make_button(api, 1)
    _b2, fired2 = make_button(api, 2)
    _other, fired_other = make_button(api, 2, unit_id=8)
    bus: list = []
    hass.bus.async_listen(SWITCH_EVENT, lambda e: bus.append(e.data))

    # Goes through the library's real dispatch, like a decoded packet.
    api._casa._dataCallback(
        7, switch_event(event=event)
    )  # IncomingPacketType.SwitchEvent
    await asyncio.sleep(0)

    assert fired2 == [expected]  # exactly once, right button
    assert fired1 == []
    assert fired_other == []
    assert bus == [
        {
            "unit_id": 7,
            "button": 2,
            "event": f"button_{expected}",
            "input_mapped_event": f"button_{expected}",
        }
    ]


async def test_unknown_event_is_dropped(hass, tmp_path, caplog) -> None:
    api, _client, _ = make_api(hass, tmp_path)
    _b, fired = make_button(api, 2)
    api._switch_event_handler(switch_event(event=ButtonEventType.UNKNOWN))
    assert fired == []
    assert "Unknown switch event" in caplog.text


async def test_unregister_on_disconnect(hass, tmp_path) -> None:
    api, _client, _ = make_api(hass, tmp_path)
    api._casa.disconnect = MagicMock(side_effect=lambda: asyncio.sleep(0))
    await api.disconnect()
    assert api._switch_event_handler not in api._casa._switchEventCallbacks


def test_is_switch() -> None:
    assert is_switch(SimpleNamespace(unitType=SimpleNamespace(mode="Kinetic Switch")))
    assert not is_switch(SimpleNamespace(unitType=SimpleNamespace(mode="Dimmer")))
