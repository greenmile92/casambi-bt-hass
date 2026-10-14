"""The Casambi Bluetooth integration."""

import asyncio
from collections.abc import Callable, Iterable
import hashlib
import inspect
import logging
from pathlib import Path
from typing import Any, Final, cast

from CasambiBt import Casambi, Group, Scene, Unit, UnitControlType
from CasambiBt._switch import ButtonEventType, SwitchEvent
from CasambiBt.errors import (
    AuthenticationError,
    BluetoothDeviceNotFoundError,
    BluetoothError,
    ProtocolError,
)

from homeassistant.components import bluetooth
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.typing import ConfigType

from .const import (
    DOMAIN,
    PLATFORMS,
    RECONNECT_BACKOFF_MAX,
    RECONNECT_BACKOFF_START,
    RECONNECT_BACKOFF_STEP,
    SWITCH_EVENT,
)

_LOGGER: Final = logging.getLogger(__name__)

CARD_URL: Final = "/casambi_bt/casambi-mix-card.js"
CARD_PATH: Final = Path(__file__).parent / "casambi-mix-card.js"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve the white/color mix card and load it on every dashboard."""
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(CARD_PATH), cache_headers=True)]
    )
    # The content hash makes browsers fetch the card again after an update.
    card = await hass.async_add_executor_job(CARD_PATH.read_bytes)
    add_extra_js_url(hass, f"{CARD_URL}?v={hashlib.sha256(card).hexdigest()[:8]}")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Casambi Bluetooth from a config entry."""
    api = CasambiApi(hass, entry, entry.data[CONF_ADDRESS], entry.data[CONF_PASSWORD])
    await api.connect()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = api

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    casa_api: CasambiApi = hass.data[DOMAIN][entry.entry_id]
    await casa_api.disconnect()

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    device_entry: DeviceEntry,
) -> bool:
    """Allow removing a device that is no longer part of this Casambi network."""
    api: CasambiApi = hass.data[DOMAIN][config_entry.entry_id]
    live = {(DOMAIN, api.casa.networkId)}
    live |= {(DOMAIN, unit.uuid) for unit in api.casa.units}
    return not (device_entry.identifiers & live)


def get_cache_dir(hass: HomeAssistant) -> Path:
    """Return the cache dir that should be used by CasambiBt."""
    conf_path = Path(hass.config.config_dir)
    return conf_path / ".storage" / DOMAIN


class CasambiApi:
    """Defines a Casambi API."""

    def __init__(
        self,
        hass: HomeAssistant,
        conf_entry: ConfigEntry,
        address: str,
        password: str,
    ) -> None:
        """Initialize a Casambi API."""

        self.hass = hass
        self.conf_entry = conf_entry
        self.address = address
        self.password = password
        self._casa = Casambi(get_async_client(hass), get_cache_dir(hass))
        self.casa = cast(Casambi, CasambiProxy(self, self._casa))

        self._callback_map: dict[int, list[Callable[[Unit], None]]] = {}
        self._switch_callback_map: dict[int, list[Callable[[SwitchEvent], None]]] = {}
        self._connection_callbacks: list[Callable[[], None]] = []
        self._cancel_bluetooth_callback: Callable[[], None] | None = None
        self._reconnect_task: asyncio.Task | None = None

    def _register_bluetooth_callback(self) -> None:
        self._cancel_bluetooth_callback = bluetooth.async_register_callback(
            self.hass,
            self._bluetooth_callback,
            {"address": self.address, "connectable": True},
            bluetooth.BluetoothScanningMode.PASSIVE,
        )

    async def connect(self) -> None:
        """Connect initially to the Casmabi network."""
        try:
            device = bluetooth.async_ble_device_from_address(
                self.hass, self.address, connectable=True
            )
            if not device:
                raise BluetoothDeviceNotFoundError  # noqa: TRY301

            self._casa.registerDisconnectCallback(self._casa_disconnect)
            self._casa.registerUnitChangedHandler(self._unit_changed_handler)
            self._casa.registerSwitchEventHandler(self._switch_event_handler)

            await self._casa.connect(device, self.password)
        except BluetoothError as err:
            raise ConfigEntryNotReady("Failed to use bluetooth") from err
        except BluetoothDeviceNotFoundError as err:
            raise ConfigEntryNotReady(
                f"Network with address {self.address} wasn't found"
            ) from err
        except AuthenticationError as err:
            raise ConfigEntryAuthFailed(
                f"Failed to authenticate to network {self.address}"
            ) from err
        except Exception as err:  # pylint: disable=broad-except
            raise ConfigEntryError(
                f"Unexpected error creating network {self.address}"
            ) from err

        # Only register bluetooth callback after connection.
        # Otherwise we get an immediate callback and attempt two connections at once.
        if not self._cancel_bluetooth_callback:
            self._register_bluetooth_callback()

    async def reconnect(self) -> None:
        """Start reconnection attempt to the Casmabi network."""
        backoff = RECONNECT_BACKOFF_START
        protocol_error = False
        while True:
            try:
                device = bluetooth.async_ble_device_from_address(
                    self.hass, self.address, connectable=True
                )
                if not device:
                    raise BluetoothDeviceNotFoundError  # noqa: TRY301

                await self._casa.reconnect(device)
                protocol_error = False
                _LOGGER.info("Reconnected to network %s.", self.address)
                self._notify_connection_change()
                break
            except BluetoothError:
                _LOGGER.debug(
                    "Connecting failed due to bluetooth error. Retrying...",
                    exc_info=True,
                )
            except BluetoothDeviceNotFoundError:
                # Sometimes HA notifications seem not to work.
                # We are still registered for HA notifications but retrying very seldomly should be fine.
                backoff = max(backoff, 60)
            except AuthenticationError as err:
                raise HomeAssistantError from err
            except asyncio.CancelledError:
                _LOGGER.debug("Reconnect task cancelled.")
                break
            except ProtocolError as err:
                # We retry once on Protocol errors to make sure they aren't permanent.
                if not protocol_error:
                    _LOGGER.debug("Retrying once on protocol error.", exc_info=True)
                    protocol_error = True
                else:
                    raise HomeAssistantError from err
            except Exception as err:  # pylint: disable=broad-except
                raise HomeAssistantError from err

            await asyncio.sleep(backoff)
            backoff = min(RECONNECT_BACKOFF_MAX, backoff * RECONNECT_BACKOFF_STEP)

    @property
    def available(self) -> bool:
        """Return True if the controller is available."""
        return self._casa.connected

    def get_units(
        self, control_types: list[UnitControlType] | None = None
    ) -> Iterable[Unit]:
        """Return all units in the network optionally filtered by control type."""

        if not control_types:
            return self._casa.units

        return filter(
            lambda u: any(uc.type in control_types for uc in u.unitType.controls),  # type: ignore[arg-type]
            self._casa.units,
        )

    def get_groups(self) -> Iterable[Group]:
        """Return all groups in the network."""

        return self._casa.groups

    def get_scenes(self) -> Iterable[Scene]:
        """Return all scenes in the network."""

        return self._casa.scenes

    async def disconnect(self) -> None:
        """Disconnects from the controller and disables automatic reconnect."""
        if (
            self._reconnect_task is not None
            and not self._reconnect_task.cancelled()
            and not self._reconnect_task.done()
        ):
            self._reconnect_task.cancel()
            try:
                await asyncio.wait_for(self._reconnect_task, 5)
            except asyncio.CancelledError:
                pass
            except TimeoutError:
                _LOGGER.debug(
                    "Timeout while cancelling reconnect as part of disconnect."
                )
            except Exception:
                _LOGGER.debug(
                    "Got exception when cancelling reconnect. Ignoring.", exc_info=True
                )

        if self._cancel_bluetooth_callback is not None:
            self._cancel_bluetooth_callback()
            self._cancel_bluetooth_callback = None

        # This needs to happen before we disconnect.
        # We don't want to be informed about disconnects initiated by us.
        self._casa.unregisterDisconnectCallback(self._casa_disconnect)

        try:
            await self._casa.disconnect()
        except Exception:
            _LOGGER.exception("Error during disconnect.")
        self._casa.unregisterUnitChangedHandler(self._unit_changed_handler)
        self._casa.unregisterSwitchEventHandler(self._switch_event_handler)

    @callback
    def _casa_disconnect(self) -> None:
        _LOGGER.info(
            "Lost connection to network %s. Scheduling reconnect.", self.address
        )
        # Notify first so the status is written before the reconnect task runs.
        self._notify_connection_change()
        self._schedule_reconnect()

    def register_connection_updates(self, c: Callable[[], None]) -> None:
        """Register a callback for connect and disconnect of the network."""
        self._connection_callbacks.append(c)

    def unregister_connection_updates(self, c: Callable[[], None]) -> None:
        """Unregister a callback for connect and disconnect of the network."""
        self._connection_callbacks.remove(c)

    def _notify_connection_change(self) -> None:
        # The library calls the disconnect callback before it resets its connection state,
        # so `connected` is still True here. Run the callbacks after the current call stack.
        for c in self._connection_callbacks:
            self.hass.loop.call_soon(c)

    def register_switch_updates(
        self, unit: Unit, c: Callable[[SwitchEvent], None]
    ) -> None:
        """Register a callback for switch events of a unit."""
        self._switch_callback_map.setdefault(unit.deviceId, []).append(c)

    def unregister_switch_updates(
        self, unit: Unit, c: Callable[[SwitchEvent], None]
    ) -> None:
        """Unregister a callback for switch events of a unit."""
        self._switch_callback_map[unit.deviceId].remove(c)

    @callback
    def _switch_event_handler(self, event: SwitchEvent) -> None:
        if event.event == ButtonEventType.UNKNOWN:
            _LOGGER.warning(
                "Unknown switch event from unit %i button %i (target type 0x%02x, flags 0x%x, data %s).",
                event.unit_id,
                event.button,
                event.target_type,
                event.flags,
                event.extra_data.hex(),
            )
            return
        _LOGGER.debug("Switch event: %s", event)
        for c in self._switch_callback_map.get(event.unit_id, []):
            c(event)
        # Kept for automations that listen to the event of the former hand-patched install.
        name = f"button_{event.event.name.lower()}"
        self.hass.bus.async_fire(
            SWITCH_EVENT,
            {
                "unit_id": event.unit_id,
                "button": event.button,
                "event": name,
                "input_mapped_event": name,
            },
        )

    def register_unit_updates(self, unit: Unit, c: Callable[[Unit], None]) -> None:
        """Register a callback for unit updates.

        :param unit: The unit for which changes should be reported.
        :param c: The callback.
        """
        self._callback_map.setdefault(unit.deviceId, []).append(c)

    def unregister_unit_updates(self, unit: Unit, c: Callable[[Unit], None]) -> None:
        """Unregister a callback for unit updates.

        :param unit: The unit for which changes should no longer be reported.
        :param c: The callback.
        """
        self._callback_map[unit.deviceId].remove(c)

    @callback
    def _unit_changed_handler(self, unit: Unit) -> None:
        if unit.deviceId not in self._callback_map:
            return
        for c in self._callback_map[unit.deviceId]:
            c(unit)

    @callback
    def _bluetooth_callback(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        _change: bluetooth.BluetoothChange,
    ) -> None:
        if not self._casa.connected and service_info.connectable:
            self._schedule_reconnect()

    def _schedule_reconnect(self) -> None:
        # We assume that a reconnect task is only cancelled when disconnected.
        # So never reconnect when the disconnect task has been cancelled.
        if self._reconnect_task is not None and self._reconnect_task.cancelled():
            _LOGGER.debug(
                "Attempted to schedule reconnect after task was canceled. Disconnect already called?"
            )
            return
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = self.conf_entry.async_create_background_task(
                self.hass,
                self.reconnect(),
                "Reconnect",
                # Must not start eagerly: the library calls the disconnect callback
                # while `connected` is still True, so an eager reconnect returns
                # immediately and no reconnect is ever attempted (#170).
                eager_start=False,
            )


class CasambiProxy:
    """Proxy the write operations so that bluetooth errors automatically trigger a reconnect."""

    def __init__(self, api: CasambiApi, casa: Casambi) -> None:
        """Initialize a CasambiProxy."""
        self._api = api
        self._casa = casa

    def __getattr__(self, name: str) -> Any:
        """Wrap all async calls to drop BluetoothError and trigger a reconnect instead."""
        attr = getattr(self._casa, name)

        if inspect.iscoroutinefunction(attr):

            async def async_wrapper(*args, **kwargs):
                try:
                    return await attr(*args, **kwargs)
                except BluetoothError:
                    _LOGGER.debug("Bluetooth error during write.", exc_info=True)
                    _LOGGER.info("Triggering reconnect after write failed.")
                    self._api._schedule_reconnect()  # noqa: SLF001

            return async_wrapper

        return attr
