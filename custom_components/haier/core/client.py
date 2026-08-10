import asyncio
import hashlib
import json
import logging
import random
import time
from datetime import datetime, timedelta
from functools import wraps
from typing import List, Dict
from urllib.parse import urlparse

import aiohttp
from dateutil.relativedelta import relativedelta
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .device import HaierDevice

_LOGGER = logging.getLogger(__name__)

# token来源客户端。refresh_token与签发它的appId绑定，用其他客户端的appId去刷新
# 会返回 43005 授权异常或失败，所以此处须与token的来源客户端一致
APP_SOURCE_WXAPP = 'wxapp'
APP_SOURCE_APP = 'app'

DEFAULT_APP_SOURCE = APP_SOURCE_WXAPP

APP_SOURCES = {
    # 微信小程序
    APP_SOURCE_WXAPP: ('MB-SHEZJAPPWXXCX-0000', '79ce99cc7f9804663939676031b8a427'),
    # App
    APP_SOURCE_APP: ('MB-UZHSH-0001', '5dfca8714eb26e3a776e58a8273c8752'),
}

REFRESH_TOKEN_API = 'https://zj.haier.net/api-gw/oauthserver/account/v1/refreshToken'
PHONE_LOGIN_API = 'https://zj.haier.net/api-gw/oauthserver/account/v1/login'
GET_USER_INFO_API = 'https://account-api.haier.net/v2/haier/userinfo'
GET_DEVICES_API = 'https://uws.haier.net/uds/v1/protected/deviceinfos'
GET_WSS_GW_API = 'https://uws.haier.net/gmsWS/wsag/assign'
GET_DIGITAL_MODEL_API = 'https://uws.haier.net/shadow/v1/devdigitalmodels'

STATS_BASE_URL = 'https://data.haier.net/bigdata-mobile-rest'
WATER_7DAY_API = f'{STATS_BASE_URL}/device/water/firewaterheater/v1/getDev7DateWaterConsumption'
WATER_30DAY_API = f'{STATS_BASE_URL}/device/water/firewaterheater/v1/getDev30DateWaterConsumption'
WATER_YEAR_API = f'{STATS_BASE_URL}/device/water/firewaterheater/v1/getDevYearWaterConsumption'
GAS_7DAY_API = f'{STATS_BASE_URL}/device/gas/firewaterheater/v1/getDev7DateGasConsumption'
GAS_30DAY_API = f'{STATS_BASE_URL}/device/gas/firewaterheater/v1/getDev30DateGasConsumption'
GAS_YEAR_API = f'{STATS_BASE_URL}/device/gas/firewaterheater/v1/getDevYearGasConsumption'
MONTHLY_API = f'{STATS_BASE_URL}/device/v2/report/firewaterheater/index/01180/period/1m/time/1m'

def retry_on_exception(exceptions, max_tries=3):
    """
    重试装饰器
    :param exceptions: 需要捕获并重试的异常（元组）
    :param max_tries: 最大尝试次数
    """

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            attempt = 0

            while attempt < max_tries:
                attempt += 1
                try:
                    return await func(*args, **kwargs)
                except exceptions as err:
                    last_exception = err
                    if attempt < max_tries:
                        _LOGGER.warning(
                            "捕获到异常 %s。进行第 %s 次重试...",
                            type(err).__name__, attempt
                        )

            _LOGGER.error("达到最大重试次数 (%s): %s", max_tries, last_exception)

            raise last_exception

        return wrapper

    return decorator


class TokenInfo:

    def __init__(self, token: str, refresh_token: str, expires_in: int, access_user_token: str = None):
        self._token = token
        self._refresh_token = refresh_token
        self._expires_in = expires_in
        self._access_user_token = access_user_token or token

    @property
    def token(self) -> str:
        return self._token

    @property
    def refresh_token(self) -> str:
        return self._refresh_token

    @property
    def expires_in(self) -> int:
        return self._expires_in
    
    @property
    def access_user_token(self) -> str:
        return self._access_user_token


class HaierClientException(Exception):
    pass


class HaierUnauthorizedException(HaierClientException):
    """Stats / bigdata API rejected the token (e.g. retCode 10401)."""


_SENSITIVE_HEADER_KEYS = frozenset({
    'accessToken',
    'Access-User-Token',
    'appKey',
    'sign',
})


def _redact_headers(headers: dict) -> dict:
    redacted = {}
    for key, value in headers.items():
        if key in _SENSITIVE_HEADER_KEYS and value:
            text = str(value)
            redacted[key] = f'{text[:4]}***{text[-4:]}' if len(text) > 8 else '***'
        else:
            redacted[key] = value
    return redacted


class HaierClient:

    def __init__(self, hass: HomeAssistant, client_id: str, token: str, access_user_token: str = None, app_source: str = DEFAULT_APP_SOURCE):
        self._client_id = client_id
        self._token = token
        self._access_user_token = access_user_token or token
        self._app_id, self._app_key = APP_SOURCES.get(app_source, APP_SOURCES[DEFAULT_APP_SOURCE])
        self._hass = hass
        self._session = async_get_clientsession(hass)

    @retry_on_exception(exceptions=(aiohttp.ClientError, asyncio.TimeoutError))
    async def phone_login(self, phone: str, password: str) -> TokenInfo:
        """
        手机号+密码登录（使用 App 来源的 appId/appKey）
        :param phone: 手机号
        :param password: 密码
        :return: TokenInfo
        """
        payload = {
            'username': phone,
            'password': password
        }

        headers = await self._generate_common_headers(PHONE_LOGIN_API, json.dumps(payload))
        async with self._session.post(url=PHONE_LOGIN_API, headers=headers, json=payload) as response:
            content = await response.json(content_type=None)
            self._assert_response_successful(content)

            token_info = content['data']['tokenInfo']
            return TokenInfo(
                token_info['accountToken'],
                token_info['refreshToken'],
                token_info['expiresIn']
            )

    @retry_on_exception(exceptions=(aiohttp.ClientError, asyncio.TimeoutError))
    async def refresh_token(self, refresh_token: str) -> TokenInfo:
        """
        刷新token
        :return:
        """
        payload = {
            'refreshToken': refresh_token
        }

        headers = await self._generate_common_headers(REFRESH_TOKEN_API, json.dumps(payload))
        async with self._session.post(url=REFRESH_TOKEN_API, headers=headers, json=payload) as response:
            content = await response.json(content_type=None)
            self._assert_response_successful(content)

            token_info = content['data']['tokenInfo']
            return TokenInfo(
                token_info['accountToken'],
                token_info['refreshToken'],
                token_info['expiresIn']
            )

    @retry_on_exception(exceptions=(aiohttp.ClientError, asyncio.TimeoutError))
    async def get_user_info(self) -> dict:
        """
        根据token获取用户信息
        :return:
        """
        headers = {
            'Authorization': f'Bearer {self._token}',
        }
        async with self._session.get(url=GET_USER_INFO_API, headers=headers) as response:
            content = await response.json(content_type=None)
            if 'error_description' in content:
                raise HaierClientException('Error getting user info, error: {}'.format(content['error_description']))

            return {
                'userId': content['userId'],
                'mobile': content['mobile'],
                'username': content['username']
            }

    @retry_on_exception(exceptions=(aiohttp.ClientError, asyncio.TimeoutError))
    async def get_devices(self) -> List[HaierDevice]:
        """
        获取设备列表
        """
        headers = await self._generate_common_headers(GET_DEVICES_API)
        async with self._session.get(url=GET_DEVICES_API, headers=headers) as response:
            content = await response.json(content_type=None)
            self._assert_response_successful(content)

            devices = []
            init_tasks = []
            for raw in content['deviceinfos']:
                _LOGGER.debug('Device Info: {}'.format(raw))
                device = HaierDevice(self, raw)
                devices.append(device)
                init_tasks.append(device.async_init())

            await asyncio.gather(*init_tasks, return_exceptions=True)

            return devices

    @retry_on_exception(exceptions=(aiohttp.ClientError, asyncio.TimeoutError))
    async def get_digital_model(self, deviceId: str) -> list:
        """
        获取设备attributes
        :param deviceId:
        :return:
        """
        payload = {
            'deviceInfoList': [
                {
                    'deviceId': deviceId
                }
            ]
        }

        headers = await self._generate_common_headers(GET_DIGITAL_MODEL_API, json.dumps(payload))
        async with self._session.post(url=GET_DIGITAL_MODEL_API, json=payload, headers=headers) as response:
            content = await response.json(content_type=None)
            self._assert_response_successful(content)

            if deviceId not in content['detailInfo']:
                _LOGGER.warning("Device {} get digital model fail. response: {}".format(
                    deviceId,
                    json.dumps(content, ensure_ascii=False)
                ))
                return []

            return json.loads(content['detailInfo'][deviceId])['attributes']

    async def get_digital_model_from_cache(self, device: HaierDevice) -> list:
        store = Store(self._hass, 1, 'haier/device_{}.json'.format(device.id))
        cache = None
        try:
            cache = await store.async_load()
            if isinstance(cache, str):
                raise RuntimeError('cache is invalid')
            if not isinstance(cache, dict) or cache.get('_cache_version') != 2:
                _LOGGER.info("Device %s cache version mismatch or outdated, will re-fetch", device.id)
                cache = None
        except Exception:
            _LOGGER.warning("Device %s cache is invalid", device.id)
            await store.async_remove()
            cache = None

        if cache:
            _LOGGER.info("Device %s get digital model from cache successful", device.id)
            return cache['attributes']

        _LOGGER.info("Device %s get digital model from cache fail, attempt to obtain remotely", device.id)
        attributes = await self.get_digital_model(device.id)
        await store.async_save({
            '_cache_version': 2,
            'device': {
                'name': device.name,
                'type': device.type,
                'product_code': device.product_code,
                'product_name': device.product_name,
                'wifi_type': device.wifi_type
            },
            'attributes': attributes
        })

        return attributes

    @retry_on_exception(exceptions=(aiohttp.ClientError, asyncio.TimeoutError))
    async def get_device_snapshot_data(self, deviceId: str) -> dict:
        """
        获取指定设备最新的属性数据
        :param deviceId:
        :return:
        """
        values = {}

        attributes = await self.get_digital_model(deviceId)
        # 从attributes中读取实体值
        for attribute in attributes:
            if 'value' not in attribute:
                continue

            values[attribute['name']] = attribute['value']

        return values

    @retry_on_exception(exceptions=(aiohttp.ClientError, asyncio.TimeoutError))
    async def get_devices_online_status(self) -> Dict[str, bool]:
        """
        获取所有设备的在线状态
        :return:
        """
        headers = await self._generate_common_headers(GET_DEVICES_API)
        async with self._session.get(url=GET_DEVICES_API, headers=headers) as response:
            content = await response.json(content_type=None)
            self._assert_response_successful(content)

            devices = {}
            for device in content['deviceinfos']:
                devices[device['deviceId']] = device['online']

            return devices

    @retry_on_exception(exceptions=(aiohttp.ClientError, asyncio.TimeoutError))
    async def get_device_gateway(self) -> str:
        """
        获取网关地址
        :return:
        """
        payload = {
            'clientId': self._client_id,
            'token': self._token
        }

        headers = await self._generate_common_headers(GET_WSS_GW_API, json.dumps(payload))
        async with self._session.post(url=GET_WSS_GW_API, json=payload, headers=headers) as response:
            content = await response.json(content_type=None)
            self._assert_response_successful(content)

            return content['agAddr'].replace('http://', 'wss://')

    @staticmethod
    def _raise_if_stats_unauthorized(content: dict, context: str):
        ret_code = str(content.get('retCode', ''))
        ret_info = content.get('retInfo')
        if ret_code in ('10401', '401') or str(ret_info).upper() == 'UNAUTHORIZED':
            raise HaierUnauthorizedException(
                f'{context} unauthorized (retCode={ret_code}, retInfo={ret_info}). '
                'Check Access User Token / app source match for bigdata APIs.'
            )

    @retry_on_exception(exceptions=(aiohttp.ClientError, asyncio.TimeoutError))
    async def get_consumption_data(self, device_id: str, api: str) -> dict:
        payload = {
            'mac': device_id
        }

        headers = await self._generate_stats_headers(api, json.dumps(payload))
        _LOGGER.debug(
            'STATS REQUEST: %s payload=%s headers=%s',
            api, json.dumps(payload), _redact_headers(headers),
        )
        async with self._session.post(url=api, json=payload, headers=headers) as response:
            text = await response.text()
            _LOGGER.debug(
                'STATS RESPONSE: status=%d body_len=%d text=%s',
                response.status, len(text), text[:300] if text else '(empty)',
            )
            if not text:
                return {'indexList': [], 'unit': None}

            content = json.loads(text)
            _LOGGER.debug(
                'STATS PARSED: retCode=%s data_count=%d',
                content.get('retCode'), len(content.get('data') or []),
            )
            self._raise_if_stats_unauthorized(content, 'consumption')
            if content.get('retCode') != '1000':
                raise HaierClientException('Error getting consumption data: {}'.format(content.get('retInfo')))

            if not content.get('data'):
                return {'indexList': [], 'unit': None}

            return {
                'indexList': content['data'][0].get('indexList', []),
                'unit': content['data'][0].get('unit')
            }

    @retry_on_exception(exceptions=(aiohttp.ClientError, asyncio.TimeoutError))
    async def get_monthly_consumption_data(self, device_id: str, api: str, month: str = None) -> dict:
        if month is None:
            from datetime import datetime
            now = datetime.now()
            month = now.strftime('%Y%m')

        payload = {
            'mac': device_id,
            'month': month
        }

        headers = await self._generate_stats_headers(api, json.dumps(payload))
        _LOGGER.debug(
            'MONTHLY STATS REQUEST: %s payload=%s headers=%s',
            api, json.dumps(payload), _redact_headers(headers),
        )
        async with self._session.post(url=api, json=payload, headers=headers) as response:
            text = await response.text()
            _LOGGER.debug(
                'MONTHLY STATS RESPONSE: status=%d body_len=%d text=%s',
                response.status, len(text), text[:300] if text else '(empty)',
            )
            if not text:
                return {'waterConsumption': 0, 'gasConsumption': 0, 'month': month}

            content = json.loads(text)
            _LOGGER.debug('MONTHLY STATS PARSED: retCode=%s', content.get('retCode'))
            self._raise_if_stats_unauthorized(content, 'monthly consumption')
            if content.get('retCode') != '1000':
                raise HaierClientException('Error getting monthly consumption data: {}'.format(content.get('retInfo')))

            if not content.get('data') or not content['data'][0].get('indexList'):
                return {'waterConsumption': 0, 'gasConsumption': 0, 'month': month}

            index_item = content['data'][0]['indexList'][0]
            return {
                'waterConsumption': float(index_item.get('waterConsumption', 0)),
                'gasConsumption': float(index_item.get('gasConsumption', 0)),
                'month': index_item.get('statisticsDt', month)
            }

    @retry_on_exception(exceptions=(aiohttp.ClientError, asyncio.TimeoutError))
    async def get_yearly_monthly_consumption(self, device_id: str, api: str) -> list:
        from datetime import datetime
        now = datetime.now()
        monthly_data = []

        for i in range(12):
            target_date = now - relativedelta(months=i)
            month_str = target_date.strftime('%Y%m')

            payload = {
                'mac': device_id,
                'month': month_str
            }

            headers = await self._generate_stats_headers(api, json.dumps(payload))
            try:
                async with self._session.post(url=api, json=payload, headers=headers) as response:
                    text = await response.text()
                    if not text:
                        continue

                    content = json.loads(text)
                    # Fail fast on auth errors so callers can mark sensors unavailable
                    if i == 0:
                        self._raise_if_stats_unauthorized(content, 'yearly monthly consumption')
                    if content.get('retCode') != '1000':
                        continue

                    if not content.get('data') or not content['data'][0].get('indexList'):
                        continue

                    index_item = content['data'][0]['indexList'][0]
                    monthly_data.append({
                        'waterConsumption': float(index_item.get('waterConsumption', 0)),
                        'gasConsumption': float(index_item.get('gasConsumption', 0)),
                        'month': index_item.get('statisticsDt', month_str)
                    })
            except HaierUnauthorizedException:
                raise
            except Exception:
                _LOGGER.exception('Failed to fetch monthly data for %s', month_str)
                continue

        return monthly_data

    async def _generate_stats_headers(self, api, body=''):
        """
        大数据用量接口鉴权头。

        与设备控制不同：data.haier.net 始终要求微信小程序 appId/appKey 签名
        （与 8506d56 及之前行为一致）。若跟随 app_source 使用 App 凭证，
        会返回 retCode=10401 UNAUTHORIZED。
        """
        timestamp = str(int(time.time() * 1000))
        sequence_id = time.strftime('%Y%m%d%H%M%S') + str(random.randint(100000, 999999))
        stats_app_id, stats_app_key = APP_SOURCES[APP_SOURCE_WXAPP]

        return {
            'accessToken': self._token,
            'appId': stats_app_id,
            'appKey': stats_app_key,
            'clientId': self._client_id,
            'sequenceId': sequence_id,
            'sign': self._sign(stats_app_id, stats_app_key, timestamp, body, api),
            'timestamp': timestamp,
            'language': 'zh-cn',
            'Access-User-Token': self._access_user_token,
        }

    async def _generate_common_headers(self, api, body=''):
        """
        返回通用headers
        :param api:
        :param body:
        :return:
        """
        timestamp = str(int(time.time() * 1000))
        # 报文流水(客户端唯一)客户端交易流水号。20位,
        # 前14位时间戳（格式：yyyyMMddHHmmss）,
        # 后6位流水号。交易发生时,根据交易 笔数自增量。App应用访问uws接口时必须确保每次请求唯一，不能重复。
        sequence_id = time.strftime('%Y%m%d%H%M%S') + str(random.randint(100000, 999999))

        return {
            'accessToken': self._token,
            'appId': self._app_id,
            'appKey': self._app_key,
            'clientId': self._client_id,
            'sequenceId': sequence_id,
            'sign': self._sign(self._app_id, self._app_key, timestamp, body, api),
            'timestamp': timestamp,
            'timezone': '+8',
            'language': 'zh-CN'
        }

    @staticmethod
    def _assert_response_successful(resp):
        if 'retCode' in resp and resp['retCode'] != '00000':
            raise HaierClientException('接口返回异常: ' + resp['retInfo'])

    @staticmethod
    def _sign(app_id, app_key, timestamp, body, url):
        content = urlparse(url).path \
                  + str(body).replace('\t', '').replace('\r', '').replace('\n', '').replace(' ', '') \
                  + str(app_id) \
                  + str(app_key) \
                  + str(timestamp)

        return hashlib.sha256(content.encode('utf-8')).hexdigest()
