"""Config flow for the DN-500BD MKII."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import callback

from .client import DN500BDClient, DN500BDConnectionError, DN500BDError
from .const import (
    CONF_ACK_NOTIFICATIONS,
    CONF_ACK_TIMEOUT,
    CONF_ANSWER_TIMEOUT,
    CONF_POLL_IDLE,
    CONF_POLL_PLAYING,
    CONF_REVERSE_CODE,
    DEFAULT_ACK_NOTIFICATIONS,
    DEFAULT_ACK_TIMEOUT,
    DEFAULT_ANSWER_TIMEOUT,
    DEFAULT_NAME,
    DEFAULT_POLL_IDLE,
    DEFAULT_POLL_PLAYING,
    DEFAULT_PORT,
    DEFAULT_REVERSE_CODE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


async def _probe(host: str, port: int) -> str | None:
    """Connect and try to read the MAC address. Returns MAC or None."""
    client = DN500BDClient(host, port, ack_timeout=0.5, answer_timeout=1.0)
    try:
        await client.connect()
        try:
            if await client.query_power():
                await client.query("mac")
        except DN500BDError as err:
            _LOGGER.debug("MAC query failed on %s: %s", host, err)
        return client.state.mac
    finally:
        await client.close()


class DN500BDConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for host/port/name."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = int(user_input[CONF_PORT])
            self._async_abort_entries_match({CONF_HOST: host})
            try:
                mac = await _probe(host, port)
            except DN500BDConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(mac.replace(":", "") if mac else host)
                self._abort_if_unique_id_configured(updates={CONF_HOST: host, CONF_PORT: port})
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={CONF_HOST: host, CONF_PORT: port},
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
            }
        )
        if user_input is not None:
            schema = self.add_suggested_values_to_schema(schema, user_input)
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Options."""
        return DN500BDOptionsFlow()


class DN500BDOptionsFlow(OptionsFlow):
    """Polling and protocol timing options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show options."""
        if user_input is not None:
            user_input[CONF_REVERSE_CODE] = (
                user_input.get(CONF_REVERSE_CODE, DEFAULT_REVERSE_CODE).strip()[:1]
                or DEFAULT_REVERSE_CODE
            )
            return self.async_create_entry(data=user_input)

        opts = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_POLL_PLAYING,
                    default=opts.get(CONF_POLL_PLAYING, DEFAULT_POLL_PLAYING),
                ): vol.All(vol.Coerce(float), vol.Range(min=0.5, max=10)),
                vol.Required(
                    CONF_POLL_IDLE, default=opts.get(CONF_POLL_IDLE, DEFAULT_POLL_IDLE)
                ): vol.All(vol.Coerce(float), vol.Range(min=1, max=120)),
                vol.Required(
                    CONF_ACK_TIMEOUT, default=opts.get(CONF_ACK_TIMEOUT, DEFAULT_ACK_TIMEOUT)
                ): vol.All(vol.Coerce(int), vol.Range(min=30, max=3000)),
                vol.Required(
                    CONF_ANSWER_TIMEOUT,
                    default=opts.get(CONF_ANSWER_TIMEOUT, DEFAULT_ANSWER_TIMEOUT),
                ): vol.All(vol.Coerce(int), vol.Range(min=30, max=5000)),
                vol.Required(
                    CONF_REVERSE_CODE,
                    default=opts.get(CONF_REVERSE_CODE, DEFAULT_REVERSE_CODE),
                ): str,
                vol.Required(
                    CONF_ACK_NOTIFICATIONS,
                    default=opts.get(CONF_ACK_NOTIFICATIONS, DEFAULT_ACK_NOTIFICATIONS),
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
