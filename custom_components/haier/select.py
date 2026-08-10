import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from . import async_register_entity
from .core.attribute import HaierAttribute
from .core.device import HaierDevice
from .entity import HaierAbstractEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    await async_register_entity(
        hass,
        entry,
        async_add_entities,
        Platform.SELECT,
        lambda device, attribute: HaierSelect(device, attribute)
    )


class HaierSelect(HaierAbstractEntity, SelectEntity):

    def __init__(self, device: HaierDevice, attribute: HaierAttribute):
        super().__init__(device, attribute)

        if 'value_comparison_table' not in attribute.ext.keys():
            raise ValueError('value_comparison_table must exist')

    def _update_value(self):
        data_key = self._attribute.ext.get('data_key', self._attribute.key)
        mapped = self._map_comparison_value(self._attributes_data.get(data_key))
        options = list(self._attr_options or [])
        # Out-of-range device values (e.g. windSpeedLevel=0) must not become current_option
        self._attr_current_option = mapped if mapped in options else None

    def select_option(self, option: str) -> None:
        data_key = self._attribute.ext.get('data_key', self._attribute.key)
        self._attr_current_option = option
        self._send_command({
            data_key: self._map_comparison_value(option, fallback=option)
        })

    def _map_comparison_value(self, value, fallback=None):
        value_comparison_table = self._attribute.ext.get('value_comparison_table', {})
        key = str(value)
        if key not in value_comparison_table:
            _LOGGER.debug(
                'Device [%s] attribute [%s] value [%s] not in comparison table',
                self._device.id, self._attribute.key, value,
            )
            return fallback

        return value_comparison_table.get(key)
