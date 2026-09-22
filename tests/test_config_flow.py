"""The options dialog's schema.

These need a Home Assistant new enough for ConfigFlowResult, which the
integration's manifest targets anyway. They are skipped rather than failed
where the installed core is older, so the suite still runs everywhere and
CI - which installs the real thing - is where they bite.

They exist because a selector that fails to build takes the whole form
down with it: Home Assistant answers "400: Bad Request" and the dialog
never opens, with nothing in the diff to suggest which field did it.
"""

from __future__ import annotations

import pytest

try:
    from unifi_wan import config_flow
except ImportError as err:  # pragma: no cover - depends on the installed core
    # Not importorskip: the failure comes from homeassistant rather than
    # from this module, and pytest re-raises that rather than skipping.
    pytest.skip(
        f"needs a Home Assistant with ConfigFlowResult (2024.4+): {err}",
        allow_module_level=True,
    )

from unifi_wan.const import (  # noqa: E402
    CONF_AUTO_SPEEDTEST_MINUTES,
    CONF_EXPECTED_DOWNLOAD,
    CONF_EXPECTED_UPLOAD,
    CONF_HOST,
    CONF_RATE_INTERVAL,
    CONF_SCAN_INTERVAL,
    MAX_EXPECTED_SPEED,
)


def test_a_number_field_without_a_unit_builds():
    """The regression: unit_of_measurement must be absent, not None.

    Home Assistant's schema marks the key optional but requires a string
    when present, so passing None rejected every number field in the
    dialog and the options flow stopped loading entirely.
    """
    field = config_flow._number(5, 3600)
    assert "unit_of_measurement" not in field.config


def test_a_number_field_with_a_unit_carries_it():
    field = config_flow._number(0, MAX_EXPECTED_SPEED, step=0.1, unit="Mbit/s")
    assert field.config["unit_of_measurement"] == "Mbit/s"
    assert field.config["step"] == 0.1


def test_the_whole_options_schema_builds():
    """What the user actually hits: opening the dialog.

    Building the schema is what failed, so this covers every field at
    once rather than each selector in isolation.
    """
    handler = config_flow.OptionsFlowHandler.__new__(config_flow.OptionsFlowHandler)
    handler._opt = lambda key, default=None: default
    schema = handler._schema()
    keys = {str(marker) for marker in schema.schema}
    for expected in (
        CONF_SCAN_INTERVAL,
        CONF_RATE_INTERVAL,
        CONF_EXPECTED_DOWNLOAD,
        CONF_EXPECTED_UPLOAD,
    ):
        assert expected in keys


def test_the_schema_builds_with_values_already_stored():
    """Defaults come from what is saved, so a stored value must not break it."""
    stored = {
        CONF_SCAN_INTERVAL: 30,
        CONF_RATE_INTERVAL: 0,
        CONF_EXPECTED_DOWNLOAD: 500.0,
        CONF_EXPECTED_UPLOAD: 47.5,
    }
    handler = config_flow.OptionsFlowHandler.__new__(config_flow.OptionsFlowHandler)
    handler._opt = lambda key, default=None: stored.get(key, default)
    schema = handler._schema()
    # The API key field carries no default, so it has nothing to call.
    defaults = {
        str(marker): marker.default()
        for marker in schema.schema
        if callable(getattr(marker, "default", None))
    }
    assert defaults[CONF_EXPECTED_DOWNLOAD] == 500.0
    assert defaults[CONF_EXPECTED_UPLOAD] == 47.5
    assert defaults[CONF_SCAN_INTERVAL] == 30


def test_every_number_field_in_the_dialog_is_valid():
    """Each selector validates its own config on construction."""
    handler = config_flow.OptionsFlowHandler.__new__(config_flow.OptionsFlowHandler)
    handler._opt = lambda key, default=None: default
    schema = handler._schema()
    numbers = [
        validator
        for validator in schema.schema.values()
        if type(validator).__name__ == "NumberSelector"
    ]
    assert len(numbers) >= 5
    for field in numbers:
        assert field.config["mode"] == "box"


def test_the_add_integration_schema_builds():
    """The same helper builds the first dialog a user ever sees, so the
    fault broke adding the integration at all, not just editing it.
    """
    schema = config_flow._connection_schema({})
    keys = {str(marker) for marker in schema.schema}
    assert CONF_AUTO_SPEEDTEST_MINUTES in keys


def test_the_reconfigure_schema_builds_from_stored_values():
    schema = config_flow._connection_schema(
        {CONF_HOST: "10.0.0.1", CONF_AUTO_SPEEDTEST_MINUTES: 120}
    )
    defaults = {
        str(marker): marker.default()
        for marker in schema.schema
        if callable(getattr(marker, "default", None))
    }
    assert defaults[CONF_HOST] == "10.0.0.1"
    assert defaults[CONF_AUTO_SPEEDTEST_MINUTES] == 120
