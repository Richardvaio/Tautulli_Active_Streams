pytest_plugins = "pytest_homeassistant_custom_component"


def pytest_configure(config):
    """Mark this repository as a Home Assistant custom-integration test suite."""
    config.addinivalue_line("markers", "enable_socket: allow socket access")
