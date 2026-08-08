# 海尔智家 (Haier) Home Assistant 集成

将[海尔智家](https://www.haier.com/)设备接入 [Home Assistant](https://www.home-assistant.io/)，支持空调、热水器、窗帘及各类传感器/开关实体。

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/release/sfairy/ha_haier.svg)](https://github.com/sfairy/ha_haier/releases)
[![Validate](https://github.com/sfairy/ha_haier/actions/workflows/validate.yml/badge.svg)](https://github.com/sfairy/ha_haier/actions/workflows/validate.yml)

## 功能

- 通过海尔账号 Token 接入云端设备
- 支持平台：`climate`、`water_heater`、`cover`、`sensor`、`binary_sensor`、`switch`、`select`、`number`
- UI 配置流程（Config Flow）及设备/实体过滤选项

## 安装

### HACS（推荐）

1. 打开 HACS → Integrations → 右上角菜单 → Custom repositories
2. Repository 填写：`https://github.com/sfairy/ha_haier`
3. Category 选择：`Integration`
4. 添加后搜索 **海尔智家** 并安装
5. 重启 Home Assistant

也可使用 My Home Assistant 快捷链接：

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=sfairy&repository=ha_haier&category=integration)

### 手动安装

1. 将本仓库中的 `custom_components/haier` 目录复制到 Home Assistant 配置目录下的 `custom_components/`
2. 重启 Home Assistant

## 配置

1. 进入 **设置 → 设备与服务 → 添加集成**
2. 搜索 **海尔智家**
3. 填写 `Client Id`、`Refresh Token`（可选 `Access User Token`）
4. 按需在集成选项中配置设备过滤、实体过滤与实体名称

## 要求

- Home Assistant ≥ 2024.1.0
- 有效的海尔智家账号凭证（Client Id / Refresh Token）

## 仓库结构

```text
ha_haier/
├── custom_components/haier/
│   ├── brand/
│   ├── translations/
│   ├── manifest.json
│   └── ...
├── hacs.json
└── README.md
```

## 问题反馈

请在 [Issues](https://github.com/sfairy/ha_haier/issues) 中提交问题或建议。

## License

MIT
