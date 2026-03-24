"""Pytest configuration for the copilot proxy test suite."""


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: marks tests that make real API calls through the proxy"
    )
