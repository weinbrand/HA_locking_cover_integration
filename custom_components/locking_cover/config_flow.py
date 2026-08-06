"""Config flow for the Locking Cover integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_BOLT_LEFT,
    CONF_BOLT_RIGHT,
    CONF_NAME,
    CONF_OPEN_TIMEOUT_S,
    CONF_SOURCE_COVER,
    CONF_TENSION_TIME_MS,
    DEFAULT_NAME,
    DEFAULT_OPEN_TIMEOUT_S,
    DEFAULT_TENSION_TIME_MS,
    DOMAIN,
    MAX_OPEN_TIMEOUT_S,
    MAX_TENSION_TIME_MS,
    MIN_OPEN_TIMEOUT_S,
    MIN_TENSION_TIME_MS,
)


def _tension_time_selector() -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=MIN_TENSION_TIME_MS,
            max=MAX_TENSION_TIME_MS,
            step=50,
            unit_of_measurement="ms",
            mode=selector.NumberSelectorMode.BOX,
        )
    )


def _open_timeout_selector() -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=MIN_OPEN_TIMEOUT_S,
            max=MAX_OPEN_TIMEOUT_S,
            step=1,
            unit_of_measurement="s",
            mode=selector.NumberSelectorMode.BOX,
        )
    )


def _bolt_sensor_selector() -> selector.EntitySelector:
    return selector.EntitySelector(selector.EntitySelectorConfig(domain="binary_sensor"))


class LockingCoverConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Locking Cover."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_SOURCE_COVER])
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=DEFAULT_NAME): selector.TextSelector(),
                vol.Required(CONF_SOURCE_COVER): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="cover")
                ),
                vol.Required(CONF_BOLT_LEFT): _bolt_sensor_selector(),
                vol.Required(CONF_BOLT_RIGHT): _bolt_sensor_selector(),
                vol.Required(
                    CONF_TENSION_TIME_MS, default=DEFAULT_TENSION_TIME_MS
                ): _tension_time_selector(),
                vol.Required(
                    CONF_OPEN_TIMEOUT_S, default=DEFAULT_OPEN_TIMEOUT_S
                ): _open_timeout_selector(),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return LockingCoverOptionsFlow()


class LockingCoverOptionsFlow(OptionsFlow):
    """Allow changing bolt sensors and timings after setup.

    The source cover is intentionally not editable here: changing it would
    change what the device fundamentally wraps. Re-adding the integration is
    the supported path for that.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = {**self.config_entry.data, **self.config_entry.options}
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_BOLT_LEFT, default=current[CONF_BOLT_LEFT]
                ): _bolt_sensor_selector(),
                vol.Required(
                    CONF_BOLT_RIGHT, default=current[CONF_BOLT_RIGHT]
                ): _bolt_sensor_selector(),
                vol.Required(
                    CONF_TENSION_TIME_MS,
                    default=current.get(CONF_TENSION_TIME_MS, DEFAULT_TENSION_TIME_MS),
                ): _tension_time_selector(),
                vol.Required(
                    CONF_OPEN_TIMEOUT_S,
                    default=current.get(CONF_OPEN_TIMEOUT_S, DEFAULT_OPEN_TIMEOUT_S),
                ): _open_timeout_selector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
