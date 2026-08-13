from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tautulli_active_streams.config_flow import (
    PlexAuthError,
    PlexConnectionError,
    TautulliOptionsFlowHandler,
    _async_validate_plex,
    _normalize_base_url,
    _server_data,
    _server_unique_id,
)
from custom_components.tautulli_active_streams.api import (
    TautulliAuthError,
    TautulliConnectionError,
)
from custom_components.tautulli_active_streams.const import (
    CONF_ADVANCED_ATTRIBUTES,
    CONF_ENABLE_IP_GEOLOCATION,
    CONF_ENABLE_STATISTICS,
    CONF_EXPOSE_DETAILED_LOCATION,
    CONF_GEO_PROVIDER,
    CONF_PLEX_BASEURL,
    CONF_PLEX_ENABLED,
    CONF_PLEX_TOKEN,
    CONF_PLEX_VERIFY_SSL,
    CONF_SESSION_INTERVAL,
    CONF_STATISTICS_DAYS,
    CONF_STATISTICS_INTERVAL,
    CONF_STATS_MONTH_TO_DATE,
    DEFAULT_STATISTICS_DAYS,
    DOMAIN,
    GEO_PROVIDER_IP_API,
)

SERVER_ID = "plex-server-id"
TAUTULLI_URL = "http://tautulli:8181"
PLEX_URL = "http://plex:32400"
API_KEY = "tautulli-api-key"
PLEX_TOKEN = "plex-token-value-1234567890"

SERVER_RESPONSE = {
    "response": {
        "result": "success",
        "data": {
            "pms_identifier": SERVER_ID,
            "pms_name": "Plex Test",
            "pms_url": PLEX_URL,
        },
    }
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading custom integrations in every test."""


@pytest.fixture
def mock_tautulli_api():
    """Return a successful mocked Tautulli client."""
    with patch(
        "custom_components.tautulli_active_streams.config_flow.TautulliAPI",
        autospec=True,
    ) as api_class:
        api_class.return_value.get_server_info = AsyncMock(return_value=SERVER_RESPONSE)
        yield api_class


@pytest.fixture
def mock_setup_entry():
    """Prevent config-flow tests from setting up runtime platforms."""
    with (
        patch(
            "custom_components.tautulli_active_streams.async_setup_entry",
            new=AsyncMock(return_value=True),
        ) as setup,
        patch(
            "custom_components.tautulli_active_streams.async_unload_entry",
            new=AsyncMock(return_value=True),
        ),
    ):
        yield setup


def _entry(
    hass: HomeAssistant,
    *,
    unique_id: str = SERVER_ID,
    statistics_days: int = DEFAULT_STATISTICS_DAYS,
    plex_enabled: bool = True,
) -> MockConfigEntry:
    """Add a representative integration entry to Home Assistant."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Plex Test",
        unique_id=unique_id,
        data={
            CONF_URL: TAUTULLI_URL,
            CONF_API_KEY: API_KEY,
            CONF_VERIFY_SSL: True,
            "server_name": "Plex Test",
            CONF_PLEX_ENABLED: plex_enabled,
            CONF_PLEX_TOKEN: PLEX_TOKEN if plex_enabled else "",
            CONF_PLEX_BASEURL: PLEX_URL if plex_enabled else "",
            CONF_PLEX_VERIFY_SSL: True,
        },
        options={
            CONF_SESSION_INTERVAL: 4,
            CONF_ADVANCED_ATTRIBUTES: False,
            CONF_ENABLE_IP_GEOLOCATION: False,
            CONF_EXPOSE_DETAILED_LOCATION: False,
            CONF_GEO_PROVIDER: "tautulli",
            CONF_ENABLE_STATISTICS: False,
            CONF_STATS_MONTH_TO_DATE: False,
            CONF_STATISTICS_DAYS: statistics_days,
            CONF_STATISTICS_INTERVAL: 1800,
            CONF_PLEX_ENABLED: plex_enabled,
        },
    )
    entry.add_to_hass(hass)
    return entry


def test_normalize_base_url() -> None:
    """Test consistent URL normalization and rejection."""
    assert _normalize_base_url(" tautulli:8181/ ") == TAUTULLI_URL
    assert _normalize_base_url("HTTPS://example.com/root/") == (
        "https://example.com/root"
    )
    for invalid in (
        "",
        "ftp://example.com",
        "http://user:pass@example.com",
        "http://example.com:99999",
        "http://example.com?query=yes",
        "x y",
    ):
        with pytest.raises(ValueError):
            _normalize_base_url(invalid)


def test_server_response_helpers() -> None:
    """Test defensive server response parsing and URL fallback."""
    assert _server_data({"response": {"data": []}}) == {}
    assert _server_unique_id({}, TAUTULLI_URL) == TAUTULLI_URL


class _Response:
    """Minimal aiohttp response context manager for Plex validation tests."""

    def __init__(self, status: int) -> None:
        self.status = status
        self.read = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


async def test_validate_plex_http_responses() -> None:
    """Test Plex validation success, authentication and network failures."""
    session = MagicMock()
    session.get.return_value = _Response(200)
    await _async_validate_plex(session, PLEX_URL, PLEX_TOKEN, True)

    session.get.return_value = _Response(401)
    with pytest.raises(PlexAuthError):
        await _async_validate_plex(session, PLEX_URL, PLEX_TOKEN, True)

    session.get.return_value = _Response(500)
    with pytest.raises(PlexConnectionError):
        await _async_validate_plex(session, PLEX_URL, PLEX_TOKEN, True)

    session.get.side_effect = aiohttp.ClientConnectionError
    with pytest.raises(PlexConnectionError):
        await _async_validate_plex(session, PLEX_URL, PLEX_TOKEN, True)


@pytest.mark.parametrize(
    ("exception", "error"),
    [
        (TautulliAuthError(), "invalid_api_key"),
        (TautulliConnectionError(), "cannot_connect"),
        (RuntimeError(), "unknown"),
    ],
)
async def test_setup_recovers_from_tautulli_errors(
    hass: HomeAssistant,
    mock_tautulli_api,
    mock_setup_entry,
    exception: Exception,
    error: str,
) -> None:
    """Test setup errors can be corrected without restarting the flow."""
    mock_tautulli_api.return_value.get_server_info.side_effect = exception
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_URL: TAUTULLI_URL,
            CONF_API_KEY: API_KEY,
            CONF_VERIFY_SSL: True,
            "server_name": "",
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    mock_tautulli_api.return_value.get_server_info.side_effect = None
    mock_tautulli_api.return_value.get_server_info.return_value = SERVER_RESPONSE
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_URL: TAUTULLI_URL,
            CONF_API_KEY: API_KEY,
            CONF_VERIFY_SSL: True,
            "server_name": "",
        },
    )
    assert result["step_id"] == "features"


async def test_setup_minimal(
    hass: HomeAssistant, mock_tautulli_api, mock_setup_entry
) -> None:
    """Test setup with optional features disabled."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_URL: "tautulli:8181/",
            CONF_API_KEY: API_KEY,
            CONF_VERIFY_SSL: True,
            "server_name": "",
        },
    )
    assert result["step_id"] == "features"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_SESSION_INTERVAL: 4,
            CONF_ADVANCED_ATTRIBUTES: False,
            CONF_ENABLE_IP_GEOLOCATION: False,
            CONF_ENABLE_STATISTICS: False,
            CONF_PLEX_ENABLED: False,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Plex Test"
    assert result["data"][CONF_URL] == TAUTULLI_URL
    assert result["data"][CONF_PLEX_TOKEN] == ""
    assert result["options"][CONF_STATISTICS_DAYS] == DEFAULT_STATISTICS_DAYS
    assert "image_proxy" not in result["options"]


async def test_setup_only_shows_enabled_optional_steps(
    hass: HomeAssistant, mock_tautulli_api, mock_setup_entry
) -> None:
    """Test conditional location, statistics and Plex setup pages."""
    with patch(
        "custom_components.tautulli_active_streams.config_flow._async_validate_plex",
        new=AsyncMock(),
    ) as validate_plex:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_URL: TAUTULLI_URL,
                CONF_API_KEY: API_KEY,
                CONF_VERIFY_SSL: True,
                "server_name": "Media",
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_SESSION_INTERVAL: 5,
                CONF_ADVANCED_ATTRIBUTES: True,
                CONF_ENABLE_IP_GEOLOCATION: True,
                CONF_ENABLE_STATISTICS: True,
                CONF_PLEX_ENABLED: True,
            },
        )
        assert result["step_id"] == "location"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_GEO_PROVIDER: GEO_PROVIDER_IP_API,
                CONF_EXPOSE_DETAILED_LOCATION: False,
            },
        )
        assert result["step_id"] == "statistics"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_STATS_MONTH_TO_DATE: True,
                CONF_STATISTICS_DAYS: 30,
                CONF_STATISTICS_INTERVAL: 1800,
            },
        )
        assert result["step_id"] == "plex"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_PLEX_TOKEN: PLEX_TOKEN, CONF_PLEX_BASEURL: PLEX_URL},
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        validate_plex.assert_awaited_once()


@pytest.mark.parametrize(
    ("submitted", "validation_error", "expected_errors"),
    [
        (
            {CONF_PLEX_TOKEN: "", CONF_PLEX_BASEURL: PLEX_URL},
            None,
            {CONF_PLEX_TOKEN: "plex_token_required"},
        ),
        (
            {CONF_PLEX_TOKEN: PLEX_TOKEN, CONF_PLEX_BASEURL: "ftp://plex"},
            None,
            {CONF_PLEX_BASEURL: "invalid_url"},
        ),
        (
            {CONF_PLEX_TOKEN: PLEX_TOKEN, CONF_PLEX_BASEURL: PLEX_URL},
            PlexAuthError(),
            {CONF_PLEX_TOKEN: "invalid_plex_token"},
        ),
        (
            {CONF_PLEX_TOKEN: PLEX_TOKEN, CONF_PLEX_BASEURL: PLEX_URL},
            PlexConnectionError(),
            {"base": "cannot_connect_plex"},
        ),
    ],
)
async def test_setup_plex_errors(
    hass: HomeAssistant,
    mock_tautulli_api,
    mock_setup_entry,
    submitted: dict,
    validation_error: Exception | None,
    expected_errors: dict,
) -> None:
    """Test each Plex setup validation error."""
    with patch(
        "custom_components.tautulli_active_streams.config_flow._async_validate_plex",
        new=AsyncMock(side_effect=validation_error),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_URL: TAUTULLI_URL,
                CONF_API_KEY: API_KEY,
                CONF_VERIFY_SSL: True,
                "server_name": "",
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_SESSION_INTERVAL: 4,
                CONF_ADVANCED_ATTRIBUTES: False,
                CONF_ENABLE_IP_GEOLOCATION: False,
                CONF_ENABLE_STATISTICS: False,
                CONF_PLEX_ENABLED: True,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], submitted
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == expected_errors


async def test_setup_rejects_invalid_url_and_duplicate(
    hass: HomeAssistant, mock_tautulli_api, mock_setup_entry
) -> None:
    """Test URL recovery and duplicate protection."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_URL: "ftp://invalid",
            CONF_API_KEY: API_KEY,
            CONF_VERIFY_SSL: True,
            "server_name": "",
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_URL: "invalid_url"}

    _entry(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_URL: TAUTULLI_URL,
            CONF_API_KEY: API_KEY,
            CONF_VERIFY_SSL: True,
            "server_name": "",
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_updates_only_api_key_and_checks_identity(
    hass: HomeAssistant, mock_tautulli_api, mock_setup_entry
) -> None:
    """Test successful reauthentication and server identity protection."""
    entry = _entry(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_REAUTH,
            "entry_id": entry.entry_id,
        },
        data=entry.data,
    )
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "replacement-key"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_KEY] == "replacement-key"
    assert entry.data[CONF_URL] == TAUTULLI_URL

    other_entry = _entry(hass, unique_id="original-server")
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_REAUTH,
            "entry_id": other_entry.entry_id,
        },
        data=other_entry.data,
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "replacement-key"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_server"


@pytest.mark.parametrize(
    ("exception", "error"),
    [
        (TautulliAuthError(), "invalid_api_key"),
        (TautulliConnectionError(), "cannot_connect"),
        (RuntimeError(), "unknown"),
    ],
)
async def test_reauth_errors(
    hass: HomeAssistant,
    mock_tautulli_api,
    mock_setup_entry,
    exception: Exception,
    error: str,
) -> None:
    """Test reauthentication error handling."""
    entry = _entry(hass)
    mock_tautulli_api.return_value.get_server_info.side_effect = exception
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_REAUTH,
            "entry_id": entry.entry_id,
        },
        data=entry.data,
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "replacement-key"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}


async def test_reconfigure_preserves_api_key(
    hass: HomeAssistant, mock_tautulli_api, mock_setup_entry
) -> None:
    """Test non-authentication reconfiguration."""
    entry = _entry(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    assert result.get("step_id") == "reconfigure", result
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_URL: "http://tautulli-new:8181/",
            CONF_VERIFY_SSL: False,
            "server_name": "New name",
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_URL] == "http://tautulli-new:8181"
    assert entry.data[CONF_API_KEY] == API_KEY
    assert entry.title == "New name"


@pytest.mark.parametrize(
    ("url", "exception", "expected_errors"),
    [
        ("ftp://invalid", None, {CONF_URL: "invalid_url"}),
        (TAUTULLI_URL, TautulliAuthError(), {"base": "invalid_api_key_reauth"}),
        (TAUTULLI_URL, TautulliConnectionError(), {"base": "cannot_connect"}),
        (TAUTULLI_URL, RuntimeError(), {"base": "unknown"}),
    ],
)
async def test_reconfigure_errors(
    hass: HomeAssistant,
    mock_tautulli_api,
    mock_setup_entry,
    url: str,
    exception: Exception | None,
    expected_errors: dict,
) -> None:
    """Test reconfiguration validation and connection errors."""
    entry = _entry(hass)
    mock_tautulli_api.return_value.get_server_info.side_effect = exception
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_URL: url,
            CONF_VERIFY_SSL: True,
            "server_name": "",
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == expected_errors


async def test_options_menu_and_legacy_day_normalization(
    hass: HomeAssistant,
) -> None:
    """Test sectioned Configure menu and legacy zero-day recovery."""
    entry = _entry(hass, statistics_days=0)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "init"
    assert result["menu_options"] == [
        "general",
        "statistics",
        "privacy",
        "plex",
    ]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "general"}
    )
    assert result["step_id"] == "general"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SESSION_INTERVAL: 8, CONF_ADVANCED_ATTRIBUTES: True},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_STATISTICS_DAYS] == DEFAULT_STATISTICS_DAYS


async def test_options_statistics_and_privacy_are_conditional(
    hass: HomeAssistant,
) -> None:
    """Test optional detail pages and privacy-safe disabling."""
    entry = _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "statistics"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_ENABLE_STATISTICS: True}
    )
    assert result["step_id"] == "statistics_details"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_STATS_MONTH_TO_DATE: True,
            CONF_STATISTICS_DAYS: 45,
            CONF_STATISTICS_INTERVAL: 3600,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ENABLE_STATISTICS] is True

    entry = _entry(hass, unique_id="privacy-entry")
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            CONF_ENABLE_IP_GEOLOCATION: True,
            CONF_EXPOSE_DETAILED_LOCATION: True,
        },
    )
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "privacy"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_ENABLE_IP_GEOLOCATION: False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_EXPOSE_DETAILED_LOCATION] is False


async def test_options_disable_statistics_and_enable_privacy(
    hass: HomeAssistant,
) -> None:
    """Test the opposite conditional option branches."""
    entry = _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "statistics"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_ENABLE_STATISTICS: False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ENABLE_STATISTICS] is False

    entry = _entry(hass, unique_id="privacy-enabled")
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "privacy"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_ENABLE_IP_GEOLOCATION: True}
    )
    assert result["step_id"] == "privacy_details"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_GEO_PROVIDER: GEO_PROVIDER_IP_API,
            CONF_EXPOSE_DETAILED_LOCATION: True,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_GEO_PROVIDER] == GEO_PROVIDER_IP_API


async def test_options_plex_update_and_confirm_disable(
    hass: HomeAssistant,
) -> None:
    """Test blank-token retention and explicit credential removal."""
    entry = _entry(hass)
    with patch(
        "custom_components.tautulli_active_streams.options_flow.async_validate_plex",
        new=AsyncMock(),
    ) as validate_plex:
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "plex"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_PLEX_ENABLED: True}
        )
        assert result["step_id"] == "plex_details"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_PLEX_TOKEN: "", CONF_PLEX_BASEURL: PLEX_URL},
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert entry.data[CONF_PLEX_TOKEN] == PLEX_TOKEN
        assert validate_plex.await_args.args[2] == PLEX_TOKEN

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "plex"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_PLEX_ENABLED: False}
    )
    assert result["step_id"] == "confirm_disable_plex"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirm": False}
    )
    assert result["errors"] == {"confirm": "confirmation_required"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirm": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_PLEX_TOKEN] == ""
    assert entry.data[CONF_PLEX_BASEURL] == ""


async def test_options_plex_rejects_bad_token(hass: HomeAssistant) -> None:
    """Test that real Plex validation failures return to the form."""
    entry = _entry(hass, plex_enabled=False)
    with patch(
        "custom_components.tautulli_active_streams.options_flow.async_validate_plex",
        new=AsyncMock(side_effect=PlexAuthError),
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "plex"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_PLEX_ENABLED: True}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_PLEX_TOKEN: PLEX_TOKEN, CONF_PLEX_BASEURL: PLEX_URL},
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {CONF_PLEX_TOKEN: "invalid_plex_token"}


@pytest.mark.parametrize(
    ("submitted", "validation_error", "expected_errors"),
    [
        (
            {CONF_PLEX_TOKEN: "", CONF_PLEX_BASEURL: PLEX_URL},
            None,
            {CONF_PLEX_TOKEN: "plex_token_required"},
        ),
        (
            {CONF_PLEX_TOKEN: PLEX_TOKEN, CONF_PLEX_BASEURL: "ftp://plex"},
            None,
            {CONF_PLEX_BASEURL: "invalid_url"},
        ),
        (
            {CONF_PLEX_TOKEN: PLEX_TOKEN, CONF_PLEX_BASEURL: PLEX_URL},
            PlexConnectionError(),
            {"base": "cannot_connect_plex"},
        ),
    ],
)
async def test_options_plex_detail_errors(
    hass: HomeAssistant,
    submitted: dict,
    validation_error: Exception | None,
    expected_errors: dict,
) -> None:
    """Test missing credentials, bad URLs and Plex connection errors."""
    entry = _entry(hass, plex_enabled=False)
    with patch(
        "custom_components.tautulli_active_streams.options_flow.async_validate_plex",
        new=AsyncMock(side_effect=validation_error),
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "plex"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_PLEX_ENABLED: True}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], submitted
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == expected_errors


async def test_options_keep_plex_disabled(hass: HomeAssistant) -> None:
    """Test saving an already-disabled Plex option without confirmation."""
    entry = _entry(hass, plex_enabled=False)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "plex"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_PLEX_ENABLED: False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_PLEX_TOKEN] == ""


def test_options_missing_entry_raises(hass: HomeAssistant) -> None:
    """Test defensive handling when an options entry disappears."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})
    handler = TautulliOptionsFlowHandler(entry)
    handler.hass = hass
    with pytest.raises(RuntimeError):
        handler._entry()
