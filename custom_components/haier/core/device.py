import json
import logging
from typing import List

from .attribute import HaierAttribute, V1SpecAttributeParser

_LOGGER = logging.getLogger(__name__)


class HaierDevice:
    _raw_data: dict
    _attributes: List[HaierAttribute]

    def __init__(self, client, raw: dict):
        self._client = client
        self._raw_data = raw
        self._attributes = []

    @property
    def id(self):
        return self._raw_data['deviceId']

    @property
    def name(self):
        return self._raw_data['deviceName'] if 'deviceName' in self._raw_data else self.id

    @property
    def type(self):
        return self._raw_data['deviceType'] if 'deviceType' in self._raw_data else None

    @property
    def product_code(self):
        return self._raw_data['productCodeT'] if 'productCodeT' in self._raw_data else None

    @property
    def product_name(self):
        return self._raw_data['productNameT'] if 'productNameT' in self._raw_data else None

    @property
    def wifi_type(self):
        return self._raw_data['wifiType']

    @property
    def attributes(self) -> List[HaierAttribute]:
        return self._attributes

    async def async_init(self):
        try:
            parser = V1SpecAttributeParser()
            _LOGGER.debug("=== PARSER INIT: EXCLUDED count=%d, GLOBAL=%s, has_gas_water_heater=%s ===",
                         len(parser._EXCLUDED_ATTRIBUTE_NAMES),
                         sorted(parser._GLOBAL_ATTRIBUTES),
                         hasattr(parser, '_parse_as_gas_water_heater'))

            attributes = await self._client.get_digital_model_from_cache(self)
            _LOGGER.debug("=== DEVICE %s: got %d raw attributes ===", self.id, len(attributes))

            stats = {}
            for item in attributes:
                try:
                    name = item.get('name', '?')
                    w = item.get('writable', False)
                    vt = item['valueRange']['type']
                    attr = parser.parse_attribute(item)
                    if attr:
                        p = str(attr.platform)
                        stats[p] = stats.get(p, 0) + 1
                        self._attributes.append(attr)
                    else:
                        if name in parser._EXCLUDED_ATTRIBUTE_NAMES:
                            stats['excluded'] = stats.get('excluded', 0) + 1
                        elif name in parser._GLOBAL_ATTRIBUTES:
                            stats['global'] = stats.get('global', 0) + 1
                        else:
                            stats['dropped'] = stats.get('dropped', 0) + 1
                        _LOGGER.debug("PARSER DROP: %s (w=%s, type=%s)", name, w, vt)
                except:
                    _LOGGER.exception("Haier device %s attribute %s parsing error occurred", self.id, item['name'])

            iter = parser.parse_global(attributes)
            global_count = 0
            if iter:
                for item in iter:
                    p = str(item.platform)
                    stats[p] = stats.get(p, 0) + 1
                    global_count += 1
                    self._attributes.append(item)

            _LOGGER.debug("=== PARSER RESULT: %s (individual=%d, global=%d) ===",
                         {k: v for k, v in sorted(stats.items())},
                         sum(v for k, v in stats.items() if k not in ('excluded', 'dropped', 'global')) - global_count,
                         global_count)
        except Exception:
            _LOGGER.exception('Haier device %s init failed', self.id)

    def __str__(self) -> str:
        return json.dumps({
            'id': self.id,
            'name': self.name,
            'type': self.type,
            'product_code': self.product_code,
            'product_name': self.product_name,
            'wifi_type': self.wifi_type
        })
