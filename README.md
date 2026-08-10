# 海尔智家 (Haier) Home Assistant 集成

将[海尔智家](https://www.haier.com/)设备接入 [Home Assistant](https://www.home-assistant.io/)，支持空调、热水器、窗帘及各类传感器/开关实体。

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/release/sfairy/ha_haier.svg)](https://github.com/sfairy/ha_haier/releases)

## 功能

- 账号密码登录，或手动填写 Token 接入云端设备
- 自动刷新 Token
- 支持平台：`climate`、`water_heater`、`cover`、`sensor`、`binary_sensor`、`switch`、`select`、`number`
- 燃气热水器用水 / 用气统计传感器（日、月、年）
- UI 配置：设备筛选、实体筛选、实体名称、偏好设置

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
3. 选择登录方式：
   - **账号密码（推荐）**：输入海尔智家手机号和密码（密码仅用于本次登录，不会保存）
   - **手动 Token（高级）**：填写 `Client Id`、`Refresh Token`，并选择 Token 来源（微信小程序 / App）
4. 可选填写 `Access User Token`（用水/用气统计接口需要；见下文）
5. 按需在集成选项中配置设备过滤、实体过滤、实体名称与偏好设置

### Access User Token（用水/用气统计）

设备控制使用登录时的 Token 来源（App / 微信小程序）凭证；用水/用气统计走 `data.haier.net`，签名固定使用微信小程序 `appId`（与历史可用版本一致）。

若日/月/年用量传感器仍显示 **不可用**，且日志出现 `Stats unauthorized` / `retCode=10401`：

1. 用抓包工具打开海尔智家，进入该热水器的用水/用气统计页
2. 找到发往 `data.haier.net/bigdata-mobile-rest/...` 的请求
3. 复制请求头中的 `Access-User-Token`（注意可能与 `accessToken` 不同）
4. 在集成选项 → **更新账户** → 手动 Token 中填入并保存

手动填写 Token 时，Token 来源（`app_source`）须与抓包客户端一致，否则刷新接口可能鉴权失败。

## 要求

- Home Assistant ≥ 2024.1.0
- 有效的海尔智家账号（手机号密码，或 Client Id / Refresh Token）

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
