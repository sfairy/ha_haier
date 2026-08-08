"""
海尔智家集成调试脚本。

在脱离 Home Assistant 环境下本地调试接口与数据解析逻辑，便于排查问题。

功能：
  - 拉取 API 数据并模拟设备与传感器状态
  - 输出 JSON 便于排查登录失败、设备解析异常、实体格式问题
  - --raw：输出原始 API 响应摘要
  - --all：一次性输出全部设备；否则进入交互式选择
  - --output-file：将输出写入指定文件

运行示例：
  python debug.py --client-id "xxx" --refresh-token "yyy" --all
  python debug.py --client-id "xxx" --refresh-token "yyy" --all --raw
  python debug.py --client-id "xxx" --refresh-token "yyy" --all --output-file result.json
  python debug.py  # 无参数时进入交互式选择
"""

import sys
import os

_script_dir = os.path.dirname(os.path.abspath(__file__))
# 解决 haier 目录下 select.py 遮蔽 stdlib select 的问题
sys.path.pop(0)
if _script_dir not in sys.path:
    sys.path.append(_script_dir)

# ========== Mock Home Assistant 依赖 ==========
class _MockModule:
    def __init__(self, **attrs):
        self.__dict__.update(attrs)

_mock_const = _MockModule(
    Platform=_MockModule(
        SENSOR="sensor", BINARY_SENSOR="binary_sensor", NUMBER="number",
        SELECT="select", SWITCH="switch", CLIMATE="climate",
        WATER_HEATER="water_heater", COVER="cover",
    ),
    UnitOfTemperature=_MockModule(CELSIUS="°C"),
    PERCENTAGE="%",
    UnitOfVolume=_MockModule(CUBIC_METERS="m³"),
    UnitOfEnergy=_MockModule(KILO_WATT_HOUR="kWh"),
)

_mock_sensor = _MockModule(
    SensorDeviceClass=_MockModule(ENERGY="energy", MONETARY="monetary",
                                   TEMPERATURE="temperature", VOLUME="volume"),
    SensorStateClass=_MockModule(TOTAL="total", TOTAL_INCREASING="total_increasing",
                                  MEASUREMENT="measurement"),
)

_mock_switch = _MockModule(SwitchDeviceClass=_MockModule(SWITCH="switch"))

_mock_map = {
    "homeassistant": type(sys)("homeassistant"),
    "homeassistant.const": type(sys)("homeassistant.const"),
    "homeassistant.components": type(sys)("homeassistant.components"),
    "homeassistant.components.sensor": type(sys)("homeassistant.components.sensor"),
    "homeassistant.components.switch": type(sys)("homeassistant.components.switch"),
}
for k, v in _mock_map.items():
    sys.modules[k] = v

_mock_map["homeassistant.const"].Platform = _mock_const.Platform
_mock_map["homeassistant.const"].UnitOfTemperature = _mock_const.UnitOfTemperature
_mock_map["homeassistant.const"].PERCENTAGE = _mock_const.PERCENTAGE
_mock_map["homeassistant.const"].UnitOfVolume = _mock_const.UnitOfVolume
_mock_map["homeassistant.const"].UnitOfEnergy = _mock_const.UnitOfEnergy
_mock_map["homeassistant.components.sensor"].SensorDeviceClass = _mock_sensor.SensorDeviceClass
_mock_map["homeassistant.components.sensor"].SensorStateClass = _mock_sensor.SensorStateClass
_mock_map["homeassistant.components.switch"].SwitchDeviceClass = _mock_switch.SwitchDeviceClass

import argparse
import asyncio
import hashlib
import json
import logging
import random
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import aiohttp

import importlib.util


def _load_module(name: str, path: str, package_name: str = None):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    if package_name:
        mod.__package__ = package_name
    sys.modules[name] = mod
    if package_name:
        parent_pkg = ".".join(package_name.split(".")[:-1])
        if parent_pkg and parent_pkg not in sys.modules:
            sys.modules[parent_pkg] = type(sys)(parent_pkg)
    spec.loader.exec_module(mod)
    return mod


_helpers = _load_module("haier_helpers", os.path.join(_script_dir, "helpers.py"))

_mock_haier = type(sys)("custom_components")
_mock_haier.haier = type(sys)("custom_components.haier")
_mock_haier.haier.helpers = _helpers
sys.modules["custom_components"] = _mock_haier
sys.modules["custom_components.haier"] = _mock_haier.haier
sys.modules["custom_components.haier.helpers"] = _helpers
sys.modules["custom_components.haier"].DOMAIN = "haier"
sys.modules["custom_components.haier.const"] = type(sys)("custom_components.haier.const")
sys.modules["custom_components.haier.const"].DOMAIN = "haier"
sys.modules["custom_components.haier.const"].SUPPORTED_PLATFORMS = []
sys.modules["custom_components.haier.const"].FILTER_TYPE_EXCLUDE = "exclude"
sys.modules["custom_components.haier.const"].FILTER_TYPE_INCLUDE = "include"

_attr_module = _load_module("haier_core_attribute", os.path.join(_script_dir, "core", "attribute.py"))
HaierAttribute = _attr_module.HaierAttribute
V1SpecAttributeParser = _attr_module.V1SpecAttributeParser

_LOGGER = logging.getLogger(__name__)

APP_ID = 'MB-SHEZJAPPWXXCX-0000'
APP_KEY = '79ce99cc7f9804663939676031b8a427'

REFRESH_TOKEN_API = 'https://zj.haier.net/api-gw/oauthserver/account/v1/refreshToken'
GET_USER_INFO_API = 'https://account-api.haier.net/v2/haier/userinfo'
GET_DEVICES_API = 'https://uws.haier.net/uds/v1/protected/deviceinfos'
GET_DIGITAL_MODEL_API = 'https://uws.haier.net/shadow/v1/devdigitalmodels'

HEADERS = {"Content-Type": "application/json"}


class HaierClientException(Exception):
    pass


def _sign(app_id, app_key, timestamp, body, url):
    content = urlparse(url).path \
              + str(body).replace('\t', '').replace('\r', '').replace('\n', '').replace(' ', '') \
              + str(app_id) \
              + str(app_key) \
              + str(timestamp)
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def _build_common_headers(client_id, token, api, body=''):
    timestamp = str(int(time.time() * 1000))
    sequence_id = time.strftime('%Y%m%d%H%M%S') + str(random.randint(100000, 999999))
    return {
        'accessToken': token,
        'appId': APP_ID,
        'appKey': APP_KEY,
        'clientId': client_id,
        'sequenceId': sequence_id,
        'sign': _sign(APP_ID, APP_KEY, timestamp, body, api),
        'timestamp': timestamp,
        'Content-Type': 'application/json',
    }


def _assert_response_successful(resp):
    if 'retCode' in resp and resp['retCode'] != '00000':
        raise HaierClientException('接口返回异常: ' + resp['retInfo'])


async def refresh_token(session, client_id, refresh_token_str):
    payload = {'refreshToken': refresh_token_str}
    headers = _build_common_headers(client_id, '', REFRESH_TOKEN_API, json.dumps(payload))
    async with session.post(url=REFRESH_TOKEN_API, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as response:
        content = await response.json(content_type=None)
        _assert_response_successful(content)
        token_info = content['data']['tokenInfo']
        return token_info['accountToken'], token_info['refreshToken'], token_info['expiresIn']


async def get_user_info(session, token):
    headers = {'Authorization': f'Bearer {token}'}
    async with session.get(url=GET_USER_INFO_API, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as response:
        content = await response.json(content_type=None)
        if 'error_description' in content:
            raise HaierClientException('Error getting user info: {}'.format(content['error_description']))
        return {
            'userId': content['userId'],
            'mobile': content['mobile'],
            'username': content['username']
        }


async def get_devices(session, client_id, token):
    headers = _build_common_headers(client_id, token, GET_DEVICES_API)
    async with session.get(url=GET_DEVICES_API, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as response:
        content = await response.json(content_type=None)
        _assert_response_successful(content)
        return content['deviceinfos']


async def get_digital_model(session, client_id, token, device_id):
    payload = {'deviceInfoList': [{'deviceId': device_id}]}
    headers = _build_common_headers(client_id, token, GET_DIGITAL_MODEL_API, json.dumps(payload))
    async with session.post(url=GET_DIGITAL_MODEL_API, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as response:
        content = await response.json(content_type=None)
        _assert_response_successful(content)
        if device_id not in content['detailInfo']:
            return []
        return json.loads(content['detailInfo'][device_id])['attributes']


def parse_device_attributes(raw_attributes):
    parser = V1SpecAttributeParser()
    results = []
    stats = {'sensor': 0, 'binary_sensor': 0, 'number': 0, 'select': 0, 'switch': 0,
             'climate': 0, 'water_heater': 0, 'cover': 0, 'excluded': 0, 'global': 0, 'dropped': 0}

    for item in raw_attributes:
        try:
            name = item.get('name', '?')
            attr = parser.parse_attribute(item)
            if attr:
                p = str(attr.platform)
                stats[p] = stats.get(p, 0) + 1
                results.append(attr)
            else:
                if name in parser._EXCLUDED_ATTRIBUTE_NAMES:
                    stats['excluded'] += 1
                elif name in parser._GLOBAL_ATTRIBUTES:
                    stats['global'] += 1
                else:
                    stats['dropped'] += 1
        except Exception:
            _LOGGER.exception("属性解析错误: %s", item.get('name', '?'))

    global_items = parser.parse_global(raw_attributes)
    if global_items:
        for item in global_items:
            p = str(item.platform)
            stats[p] = stats.get(p, 0) + 1
            results.append(item)

    return results, stats


def _interactive_select(items, build_fn, data):
    while True:
        print("\n可选项目:")
        for i, item in enumerate(items):
            print(f"  {i + 1}. {item['name']} ({item['id']})")
        print("  0. 退出")
        try:
            choice = input("请选择序号: ").strip()
            if choice == "0":
                break
            idx = int(choice) - 1
            if 0 <= idx < len(items):
                output = build_fn(items[idx], data)
                print(json.dumps(output, indent=2, ensure_ascii=False))
            else:
                print("无效序号，请输入 0 到 %d" % len(items))
        except ValueError:
            print("请输入数字")
        except KeyboardInterrupt:
            print()
            break


def _write_output_file(outputs: List[Dict], path: str) -> bool:
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(outputs, indent=2, ensure_ascii=False))
        print(f"已写入: {path}")
        return True
    except OSError as e:
        print(f"写入文件失败: {e}")
        return False


async def debug_haier(args) -> None:
    client_id = args.client_id
    refresh_token_str = args.refresh_token

    if not client_id:
        client_id = input("请输入 client_id: ").strip()
    if not client_id:
        print("client_id 不能为空！")
        return

    if not refresh_token_str:
        refresh_token_str = input("请输入 refresh_token: ").strip()
    if not refresh_token_str:
        print("refresh_token 不能为空！")
        return

    print("\n正在获取 Token...")
    async with aiohttp.ClientSession() as session:
        token, new_refresh_token, expires_in = await refresh_token(session, client_id, refresh_token_str)
        print(f"Token 获取成功，过期时间: {expires_in}s")

        print("正在获取用户信息...")
        user_info = await get_user_info(session, token)
        print(f"用户: {user_info['mobile']} ({user_info['username']})")

        print("正在获取设备列表...")
        raw_devices = await get_devices(session, client_id, token)
        print(f"共获取到 {len(raw_devices)} 个设备")

        devices_data = []
        all_attributes = []
        for raw in raw_devices:
            device_id = raw['deviceId']
            device_name = raw.get('deviceName', device_id)
            print(f"\n  正在获取设备 [{device_name}] 的属性...")
            raw_attrs = await get_digital_model(session, client_id, token, device_id)
            parsed_attrs, stats = parse_device_attributes(raw_attrs)
            _LOGGER.info("设备 %s 解析结果: %s", device_id, stats)

            device_data = {
                "id": device_id,
                "name": device_name,
                "type": raw.get('deviceType'),
                "product_code": raw.get('productCodeT'),
                "product_name": raw.get('productNameT'),
                "wifi_type": raw.get('wifiType'),
                "online": raw.get('online', False),
            }
            devices_data.append(device_data)
            all_attributes.extend(parsed_attrs)

        raw_output = getattr(args, "raw", False)
        if raw_output:
            print("\n========== 原始数据摘要 ==========")
            print(f"设备数: {len(raw_devices)}")
            for d in raw_devices:
                print(f"  - {d.get('deviceName', d['deviceId'])} ({d.get('productNameT', 'N/A')})")
            print(f"总属性数: {len(all_attributes)}")
            print("==================================\n")

        device_items = [
            {"id": d["id"], "name": d["name"]}
            for d in devices_data
        ]

        output_data = {
            "user": user_info,
            "devices": devices_data,
            "parsed_attributes": [
                {
                    "key": a.key,
                    "display_name": a.display_name,
                    "platform": str(a.platform),
                    "options": a.options,
                }
                for a in all_attributes
            ],
        }

        if args.all:
            print(json.dumps(output_data, indent=2, ensure_ascii=False))
            if args.output_file:
                _write_output_file([output_data], args.output_file)
        else:
            _interactive_select(device_items, lambda item, d: d, output_data)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    for name in ("aiohttp", "asyncio"):
        logging.getLogger(name).setLevel(logging.WARNING)

    print("\n=============================================")
    print("        海尔智家(Haier)集成调试工具")
    print("=============================================")

    parser = argparse.ArgumentParser(
        description="海尔智家集成调试工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python debug.py --client-id "xxx" --refresh-token "yyy" --all
  python debug.py --client-id "xxx" --refresh-token "yyy" --all --raw
  python debug.py --client-id "xxx" --refresh-token "yyy" --all --output-file out.json
        """,
    )
    parser.add_argument("--client-id", help="海尔 client_id")
    parser.add_argument("--refresh-token", help="海尔 refresh_token")
    parser.add_argument("--all", action="store_true", help="一次性输出全部设备")
    parser.add_argument("--output-file", help="输出到文件路径")
    parser.add_argument("--raw", action="store_true", help="输出原始 API 响应摘要")

    args = parser.parse_args()

    if not args.client_id and not args.refresh_token:
        print("\n请准备以下信息:")
        print("  - client_id（从海尔智家获取）")
        print("  - refresh_token（从海尔智家获取）")
        print()

    asyncio.run(debug_haier(args))


if __name__ == "__main__":
    main()
