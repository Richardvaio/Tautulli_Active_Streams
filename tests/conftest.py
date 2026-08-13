import asyncio
import sys

import pytest
import pytest_socket

pytest_plugins = "pytest_homeassistant_custom_component"


def pytest_configure(config):
    """Mark this repository as a Home Assistant custom-integration test suite."""
    config.addinivalue_line("markers", "enable_socket: allow socket access")


@pytest.hookimpl(tryfirst=True)
def pytest_fixture_setup(fixturedef):
    """Allow the loopback socket pair required by asyncio on Windows."""
    if sys.platform == "win32" and fixturedef.argname == "event_loop":
        pytest_socket.enable_socket()
        asyncio.get_event_loop_policy()._loop_factory = asyncio.SelectorEventLoop
