import logging
from datetime import datetime, timedelta
from typing import List

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.event import async_track_time_interval

from . import async_register_entity
from .const import DOMAIN
from .core.attribute import HaierAttribute
from .core.client import (
    WATER_30DAY_API,
    WATER_YEAR_API,
    GAS_30DAY_API,
    GAS_YEAR_API,
    MONTHLY_API,
    HaierUnauthorizedException,
)
from .core.config import DeviceFilterConfig
from .core.device import HaierDevice
from .entity import HaierAbstractEntity

_LOGGER = logging.getLogger(__name__)

DAILY_YEARLY_SENSOR_DEFS = [
    ('water_daily', '日用水量', WATER_30DAY_API, 'waterConsumption', SensorDeviceClass.WATER, 1000, 'm³', 'last'),
    ('water_yearly', '年用水量', WATER_YEAR_API, 'waterConsumption', SensorDeviceClass.WATER, 1000, 'm³', 'sum'),
    ('gas_daily', '日用气量', GAS_30DAY_API, 'gasConsumption', SensorDeviceClass.GAS, 1000, 'm³', 'last'),
    ('gas_yearly', '年用气量', GAS_YEAR_API, 'gasConsumption', SensorDeviceClass.GAS, 1000, 'm³', 'sum'),
]

MONTHLY_SENSOR_DEFS = [
    ('water_monthly', '月用水量', MONTHLY_API, 'waterConsumption', SensorDeviceClass.WATER, 1000, 'm³'),
    ('gas_monthly', '月用气量', MONTHLY_API, 'gasConsumption', SensorDeviceClass.GAS, 1000, 'm³'),
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    await async_register_entity(
        hass,
        entry,
        async_add_entities,
        Platform.SENSOR,
        lambda device, attribute: HaierSensor(device, attribute)
    )

    await _async_setup_consumption_sensors(hass, entry, async_add_entities)
    await _async_setup_monthly_sensors(hass, entry, async_add_entities)


class HaierSensor(HaierAbstractEntity, SensorEntity):

    def __init__(self, device: HaierDevice, attribute: HaierAttribute):
        super().__init__(device, attribute)

    def _update_value(self):
        comparison_table = self._attribute.ext.get('value_comparison_table', {})

        value = self._attributes_data[self._attribute.key]
        if value in (None, ''):
            self._attr_native_value = None
            return

        self._attr_native_value = comparison_table[value] if value in comparison_table else value


class HaierConsumptionSensor(SensorEntity):

    _attr_should_poll = False

    def __init__(self, client, device, key, name, device_class, api_url, field_name, initial_index_list=None, divide_by=1, display_unit='L', calc_mode='sum'):
        super().__init__()
        self._client = client
        self._device_id = device.id
        self._key = key
        self._api_url = api_url
        self._field_name = field_name
        self._remove_tracker = None
        self._divide_by = divide_by
        self._calc_mode = calc_mode

        il = initial_index_list or []
        self._index_list = il
        self._attr_native_value = self._calculate_value(il, calc_mode)
        self._unit = display_unit
        self._attr_available = True

        self._attr_unique_id = '{}.{}_{}'.format(DOMAIN, device.id.lower(), key).lower()
        self._attr_name = name
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = display_unit
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.id.lower())},
        )

    def _calculate_value(self, index_list: List, calc_mode: str) -> float:
        if not index_list:
            return 0
        
        if calc_mode == 'last':
            for item in reversed(index_list):
                val = float(item.get(self._field_name, 0))
                if val > 0:
                    return val / self._divide_by
            return 0
        elif calc_mode == 'month':
            now = datetime.now()
            current_year = now.year
            current_month = now.month
            total = 0
            for item in index_list:
                date_str = item.get('date', '')
                try:
                    item_date = datetime.strptime(date_str, '%Y-%m-%d')
                    if item_date.year == current_year and item_date.month == current_month:
                        total += float(item.get(self._field_name, 0))
                except (ValueError, TypeError):
                    continue
            return total / self._divide_by
        else:
            total = sum(float(item.get(self._field_name, 0)) for item in index_list)
            return total / self._divide_by

    async def async_update(self):
        try:
            data = await self._client.get_consumption_data(self._device_id, self._api_url)
            index_list = data.get('indexList') or []
            self._attr_native_value = self._calculate_value(index_list, self._calc_mode)
            self._index_list = index_list
            self._attr_available = True
        except HaierUnauthorizedException as err:
            # Keep entity available (same as 8506d56) so UI does not show "unavailable"
            _LOGGER.warning('Stats unauthorized for [%s] device %s: %s', self._key, self._device_id, err)
        except Exception:
            _LOGGER.exception('Failed to fetch [%s] for device %s', self._key, self._device_id)

    @property
    def extra_state_attributes(self):
        return {
            'daily_data': self._index_list,
        }

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self._remove_tracker = async_track_time_interval(
            self.hass, self._async_periodic_update, timedelta(hours=3)
        )

    async def async_will_remove_from_hass(self):
        if self._remove_tracker:
            self._remove_tracker()
            self._remove_tracker = None

    async def _async_periodic_update(self, now=None):
        await self.async_update()
        self.async_write_ha_state()


async def _async_setup_consumption_sensors(hass, entry, async_add_entities):
    client = hass.data[DOMAIN].get('client')
    if not client:
        _LOGGER.warning('Haier client not found in hass.data, skipping consumption sensors')
        return

    entities = []
    devices = hass.data[DOMAIN].get('devices', [])

    for device in devices:
        if DeviceFilterConfig.is_skip(hass, entry, device.id):
            continue

        attr_keys = [a.key for a in device.attributes]
        if 'outWaterTemp' not in attr_keys or 'onOffStatus' not in attr_keys:
            continue

        for key, name, api, field, device_class, divide_by, display_unit, calc_mode in DAILY_YEARLY_SENSOR_DEFS:
            try:
                data = await client.get_consumption_data(device.id, api)
                index_list = data.get('indexList') or []
            except HaierUnauthorizedException as err:
                _LOGGER.warning(
                    'Stats unauthorized pre-fetching [%s] for device %s: %s',
                    key, device.id, err,
                )
                index_list = []
            except Exception:
                _LOGGER.exception('Failed to pre-fetch [%s] for device %s', key, device.id)
                index_list = []
            entities.append(HaierConsumptionSensor(client, device, key, name, device_class, api, field, index_list, divide_by, display_unit, calc_mode))

    if entities:
        async_add_entities(entities)


class HaierMonthlyConsumptionSensor(SensorEntity):

    _attr_should_poll = False

    def __init__(self, client, device, key, name, device_class, api_url, field_name, initial_monthly_list=None, divide_by=1, display_unit='m³'):
        super().__init__()
        self._client = client
        self._device_id = device.id
        self._key = key
        self._api_url = api_url
        self._field_name = field_name
        self._remove_tracker = None
        self._divide_by = divide_by

        self._monthly_list = initial_monthly_list or []
        self._attr_native_value = self._find_latest_nonzero()
        self._attr_available = True

        self._attr_unique_id = '{}.{}_{}'.format(DOMAIN, device.id.lower(), key).lower()
        self._attr_name = name
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = display_unit
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.id.lower())},
        )

    def _find_latest_nonzero(self) -> float:
        for item in self._monthly_list:
            val = float(item.get(self._field_name, 0))
            if val > 0:
                return val / self._divide_by
        return 0

    async def async_update(self):
        try:
            monthly_list = await self._client.get_yearly_monthly_consumption(self._device_id, self._api_url)
            self._monthly_list = monthly_list
            self._attr_native_value = self._find_latest_nonzero()
            self._attr_available = True
        except HaierUnauthorizedException as err:
            _LOGGER.warning('Stats unauthorized for monthly [%s] device %s: %s', self._key, self._device_id, err)
        except Exception:
            _LOGGER.exception('Failed to fetch monthly [%s] for device %s', self._key, self._device_id)

    @property
    def extra_state_attributes(self):
        if self._monthly_list:
            return {
                'available_months': [item.get('month', '') for item in self._monthly_list],
                'water_monthly': {item.get('month', ''): item.get('waterConsumption', 0) for item in self._monthly_list},
                'gas_monthly': {item.get('month', ''): item.get('gasConsumption', 0) for item in self._monthly_list},
            }
        return {}

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self._remove_tracker = async_track_time_interval(
            self.hass, self._async_periodic_update, timedelta(hours=3)
        )

    async def async_will_remove_from_hass(self):
        if self._remove_tracker:
            self._remove_tracker()
            self._remove_tracker = None

    async def _async_periodic_update(self, now=None):
        await self.async_update()
        self.async_write_ha_state()


async def _async_setup_monthly_sensors(hass, entry, async_add_entities):
    client = hass.data[DOMAIN].get('client')
    if not client:
        _LOGGER.warning('Haier client not found in hass.data, skipping monthly sensors')
        return

    entities = []
    devices = hass.data[DOMAIN].get('devices', [])

    for device in devices:
        if DeviceFilterConfig.is_skip(hass, entry, device.id):
            continue

        attr_keys = [a.key for a in device.attributes]
        if 'outWaterTemp' not in attr_keys or 'onOffStatus' not in attr_keys:
            continue

        for key, name, api, field, device_class, divide_by, display_unit in MONTHLY_SENSOR_DEFS:
            try:
                monthly_list = await client.get_yearly_monthly_consumption(device.id, api)
            except HaierUnauthorizedException as err:
                _LOGGER.warning(
                    'Stats unauthorized pre-fetching monthly [%s] for device %s: %s',
                    key, device.id, err,
                )
                monthly_list = []
            except Exception:
                _LOGGER.exception('Failed to pre-fetch monthly [%s] for device %s', key, device.id)
                monthly_list = []
            entities.append(HaierMonthlyConsumptionSensor(client, device, key, name, device_class, api, field, monthly_list, divide_by, display_unit))

    if entities:
        async_add_entities(entities)





