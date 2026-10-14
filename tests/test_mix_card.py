"""The integration serves the white/color mix card and loads it on every dashboard."""

import asyncio
import json
from pathlib import Path
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL, UrlManager
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

sys.path.insert(0, str(Path(__file__).parents[1]))

import custom_components.casambi_bt as integration


@pytest.fixture
async def hass(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    yield hass
    await asyncio.sleep(0)


async def test_setup_serves_the_card_and_adds_it_to_the_frontend(hass) -> None:
    hass.http = MagicMock(async_register_static_paths=AsyncMock())
    hass.data[DATA_EXTRA_MODULE_URL] = UrlManager(lambda *_: None, [])

    assert await integration.async_setup(hass, {})

    (configs,) = hass.http.async_register_static_paths.call_args.args
    assert configs == [
        StaticPathConfig(integration.CARD_URL, str(integration.CARD_PATH))
    ]
    assert integration.CARD_PATH.is_file()
    (url,) = hass.data[DATA_EXTRA_MODULE_URL].urls
    path, version = url.split("?v=")
    assert path == integration.CARD_URL
    assert len(version) == 8


def test_frontend_is_set_up_before_the_integration() -> None:
    """add_extra_js_url fails without the frontend, and the card needs hass.http."""
    manifest = json.loads((integration.CARD_PATH.parent / "manifest.json").read_text())
    assert {"frontend", "http"} <= set(manifest["dependencies"])
