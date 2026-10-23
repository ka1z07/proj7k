# 0041. 在线排名与 pp 经曲库站点代理

- **状态**：accepted（2026-10-23；Kai：「研究连接 Oauth」，读完三个方案后选 A「经 7k.kaiz.dev 代理」）。
- **背景**：桌面端主页（ADR-0034）只读 osu!lazer 本机数据库，全球排名、pp 这类在线数据要走 osu! API v2，而 API 要 OAuth 应用。玩家装的是 GitHub Release，安装包里不能放 client secret。曲库爬虫（ADR-0026）已经用 Kai 的 OAuth 应用（客户端凭证模式，`public` 权限，`OSU_CLIENT_ID`/`OSU_CLIENT_SECRET` 在 meow 的用户环境变量里）。桌面端也已经从 lazer 成绩里拿到玩家的 osu! user id。

## 选项

| | A. 经曲库站点代理 | B. 每台机器自填 OAuth 应用 | C. 「用 osu! 登录」（授权码模式） |
|---|---|---|---|
| 玩家要做什么 | 什么都不用 | 去 osu! 设置注册应用，把 id/secret 填进桌面端 | 点登录、在 osu! 上同意 |
| 密钥 | 只在 Kai 的服务器上 | 每人自己的 | 换令牌那一步仍需 secret，得经服务器中转 |
| 能拿到什么 | 任何玩家的公开资料（排名、pp、7K 单独排名、成绩） | 同 A | 另有 `identify`、好友等私有权限 |
| 依赖 | 7k.kaiz.dev 在线 | 无 | 7k.kaiz.dev 在线 |

公开数据用 `public` 权限就能读，不需要玩家本人授权，所以 A 门槛最低。

## 决定

1. 曲库站点新增 `GET /api/players/<user id>`（`proj7k.catalog.players`）：用站点进程环境里的 `OSU_CLIENT_ID`/`OSU_CLIENT_SECRET` 调 `GET /api/v2/users/{id}/mania?key=id`，只回主页要的字段：mania 总 pp、全球/国家排名、准确率、游玩次数、等级、评级计数、各键数（`statistics.variants`，含 4K/7K）的 pp 与排名、近 90 天排名曲线。
2. 每个玩家的结果缓存 10 分钟（`Cache-Control: max-age=600`，Cloudflare 也可以缓存）；osu! 出错的结果缓存 1 分钟。对 osu! 的请求串行、至少间隔 1 秒，排队超过 10 秒的直接回 503，陌生 id 刷不爆 osu! 的限额。没配凭证时接口回 503，网站其余部分照常。
3. 桌面端/面板新增 `GET /api/me/online/<user id>`（`player.OnlineProfile`）：转问曲库站点（`PROJ7K_CATALOG_URL` 可改地址，默认 `https://7k.kaiz.dev`），本机再缓存 10 分钟，失败缓存 2 分钟。主页先画出来，再异步填一行在线数据（7K 全球/国内排名、7K pp、mania 全球排名与 pp）；拿不到就不显示，不报错。

## 后果

- 玩家零设置；Kai 不需要新注册应用。
- 主页的在线数据依赖 meow 和隧道在线；离线时只是少一行。
- 曲库站点的进程要能读到 `OSU_CLIENT_ID`/`OSU_CLIENT_SECRET`（计划任务以 Kai 的账号运行，用户环境变量就够）。部署要重启曲库。
