# 0041. 用 osu! 登录：授权码经曲库站点中转

- **状态**：accepted（2026-10-23；Kai：「研究连接 Oauth」。先选了「经 7k.kaiz.dev 代理」，又问「可以让玩家自己登录吗？」，最后选「必须登录才显示」）。
- **背景**：桌面端主页（ADR-0034）只读 osu!lazer 本机数据库，全球排名、pp 这类在线数据要走 osu! API v2，而 API 要 OAuth 应用。玩家装的是 GitHub Release，安装包里不能放 client secret。曲库爬虫（ADR-0026）已经在用 Kai 的 OAuth 应用（客户端凭证模式，`OSU_CLIENT_ID`/`OSU_CLIENT_SECRET` 在 meow 的用户环境变量里）。

## 选项

| | A. 站点代理公开资料 | B. 每台机器自填 OAuth 应用 | C. 用 osu! 登录（授权码模式） |
|---|---|---|---|
| 玩家要做什么 | 什么都不用 | 去 osu! 注册应用，把 id/secret 填进桌面端 | 点登录、在 osu! 上同意 |
| 密钥 | 只在站点 | 每人自己的 | 只在站点（换令牌和续期经站点） |
| 身份 | 从 lazer 成绩猜 user id | 同 A | osu! 确认的本人（`identify`） |

Kai 要的是「登录了才显示」：在线数据属于登录的本人，不再按 lazer 里的名字去查任何人。

## 决定

1. **站点只代管密钥，不存令牌**（`proj7k.catalog.oauth`）：
   - `GET /oauth/start?port=&nonce=`：302 到 `osu.ppy.sh/oauth/authorize`，`scope=public identify`，`state=<本机端口>.<nonce>`。端口限 1024–65535，nonce 限 16–64 位 URL 安全字符。
   - `GET /oauth/callback`：osu! 带 code 回来，站点用密钥换令牌，令牌放在内存里一个一次性交接码下面（5 分钟），浏览器被转到 `http://127.0.0.1:<端口>/oauth/done?nonce=&handoff=`。令牌本身不进任何 URL。玩家拒绝或换令牌失败时带 `error` 转回。
   - `POST /oauth/redeem {handoff}`：交出令牌，只能取一次。`POST /oauth/refresh {refresh_token}`：用密钥续期。
   - 所有这些回答都是 `no-store`。没配凭证时 `/oauth/*` 回 503。`serve --public-url`（默认 `https://7k.kaiz.dev`）决定回调地址，必须和 osu! 应用里登记的 Callback URL 一致。
2. **令牌只在玩家电脑上**（`player.OsuAccount`）：存 `~/.proj7k/osu_account.json`（权限 600）。`/oauth/done` 校验 nonce 是本机发起、15 分钟内、只用一次，再去 redeem。访问令牌过期前 2 分钟经站点续期。
3. **在线数据直接问 osu!**：用玩家自己的令牌调 `GET /api/v2/me/mania`，缓存 10 分钟（失败 2 分钟），只取主页要的字段：mania 总 pp、全球/国家排名、准确率、游玩次数、等级、各键数（`statistics.variants`，含 4K/7K）的 pp 与排名、近 90 天排名曲线。osu! 回 401（玩家在 osu! 上撤销了授权）就当作已退出。
4. **主页**：`GET /api/me/online` 回 `{signed_in:false, login_url}` 或 `{signed_in:true, user, profile}`；主页先画出来再异步填这一行。未登录显示「用 osu! 登录」（在系统浏览器里打开），已登录显示 7K 全球/国内排名、7K pp、mania 全球排名与 pp 和「退出登录」；连不上 osu! 时只说一句，不报错。

## 后果

- Kai 要在 osu! 设置的 OAuth 应用里把 Callback URL 设成 `https://7k.kaiz.dev/oauth/callback`（爬虫不用回调，不受影响）。
- 登录与续期依赖 meow 和隧道在线；登录后读排名不经站点。
- 曲库站点的进程要能读到 `OSU_CLIENT_ID`/`OSU_CLIENT_SECRET`（计划任务以 Kai 的账号运行，用户环境变量就够）。合并后要重新部署曲库。
- Cloudflare 不能缓存 `/oauth/*`（回答都是 `no-store`；POST 本来就不缓存）。
