# 0024. 统一 Web 控制台：live、sync、downscaler、profiler 合到一个页面

- **状态**：accepted（2026-10-08；扩展 ADR-0010 的实时面板服务）。
- **背景**：四个面向玩家的功能各有一条命令行：`proj7k.live`（网页雷达）、`proj7k.sync`（写 lazer 曲库）、`proj7k.downscaler`（降阶练习谱）、`proj7k.profiler`（回放诊断、画像、导入）。主要用户在中文 Windows 上，`PYTHONPATH=src python3 -m ...` 加一串参数的门槛高，路径里的空格与中文还要反复转义。只有 live 有网页。

## 决策内容

1. **以 live 服务为底座，不另起框架。** 新包 `proj7k.dashboard`：`DashboardServer` 继承 `LiveServer`，同一端口（默认 7770）同一条 WebSocket 既推实时雷达帧，也推任务帧。`LiveServer` 为此只加了两个扩展点（`_route`、`_handle_message`）与可配置的消息上限，行为不变。入口 `python3 -m proj7k.dashboard`（Windows 下可直接双击仓库根目录的 `dashboard.cmd`），默认打开浏览器；`proj7k.live` 照旧可单独运行。
2. **页面**：`/` 是控制台外壳，四个页签各是一张独立页面（`/live`、`/sync`、`/downscaler`、`/profiler`），用 iframe 载入一次后常驻，切页签不打断实时雷达，也不打断正在跑的任务。ADR-0010 公布的 OBS 地址 `/?mode=overlay…` 仍直接落到实时雷达本身。页面零外部依赖（同 ADR-0010 第 4 条，`StaticAssetGuard` 与测试照查）。
3. **任务（job）**：同步、降阶、诊断等耗时操作作为任务提交，在**单个工作线程上按提交顺序**执行。理由：它们共用 lazer 数据库与 CPU，sync 与降阶的「加入收藏夹」都会写库，串行就不会有两个写事务撞车；ADR-0010 第 5 条要的「live 只读、写事务归 sync」依然成立——写库的仍是 `LazerSyncManager` 与下缩器自己的同步函数，带它们原有的安全写入窗口与备份。任务的日志取自工作线程上的标准 `logging` 记录，与命令行输出一致。
4. **动作复用命令行的函数，不重写逻辑。** 为此从 `downscaler.cli.main` 抽出 `run_downscale`（返回 `DownscaleRun`），从 `profiler.cli.main` 抽出 `build_practice_bundle`；命令行改为调用它们，输出不变。sync 直接用 `LazerSyncManager`，画像与导入直接用 `aggregate_macro_profile`、`run_replay_import`。
5. **文件进出**：输入可以是本机路径（服务只绑 127.0.0.1，路径就是用户自己的），也可以拖拽上传（经 WebSocket 以 base64 传入，写进任务自己的目录）。输出写到 `~/.cache/proj7k/dashboard/` 下的 `practice_maps/`、`practice_bundles/`、`replay_views/`（`--output-dir` 可改），不会随任务记录清理；任务的临时目录在 `jobs/` 下，只保留最近 50 个。下载走 `/files/<任务>/<序号>/<文件名>`，**只**提供任务登记过的输出（回放查看器按相对路径加载同目录下也登记过的音频，支持 Range 以便拖动进度条），不接受客户端给的路径。`.osz` 可一键交给系统默认程序打开（即 osu! 的导入）。
6. **安全**：浏览器允许任意网站向 `ws://127.0.0.1` 开 WebSocket，而这条连接现在能触发写库与还原，所以服务拒绝 `Origin` 与本机 `Host` 不一致的握手（无 `Origin` 的非浏览器客户端照常放行）。绑定到非本机地址时启动会警告。

## 后果

- 不在控制台里提供 sync 的常驻守护模式（`--daemon`）：它是长循环，放进串行队列会挡住其它任务；需要时仍用命令行。
- osu!lazer 的锁状态只在打开页面、点「刷新状态」与同步任务结束时探测一次，不轮询：探测会瞬间占用 lazer 的锁文件。
- 回放查看器页面原本是本地 HTML 文件，音频用相对路径引用；控制台把音频复制到页面旁边，使同一页面经 HTTP 打开也能播放。

## 补充（2026-10-08）：自动读取游戏当前谱面

7. **当前谱面由 live 提供，页面默认用它。** live 解析到游戏选中的 7K 谱后，帧的 `metadata.lazer` 带上它在曲库里的位置（`.osu` 的 MD5、存储哈希、文件路径）；控制台把游戏自己的选择（拖到雷达页上的文件不算）整理成 `game_chart` 帧推给所有页面，`/api/current` 同样可查。降阶页默认输入就是这张谱（按 MD5 交给 `run_downscale`，由 lazer 定位 `.osu`、音频与背景，`.osz` 才完整），目标段位默认比它低一段；回放页默认诊断「这张谱的最近一局」（`resolve_lazer_replay` 按存储哈希过滤）。玩家没选过来源前，来源跟着游戏走；手动改过就不再自动切换。
8. **玩家名可留空。** `resolve_lazer_replay` 不给玩家名时取曲库里 7K 回放最多的玩家，即本机玩家，而不是看过的排行榜回放。从 lazer 取的回放，查看器也从 lazer 找回音频。
