# Spec: 7K 铺面实时 8 维技法雷达画像与时序应变仪表盘系统 (Phase 2.4)

## Problem Statement

当前 `proj7k` 已具备完整的 8 维能力雷达标定与数据库无损注入能力，但音游玩家、制谱者与主播在实机选歌和游玩过程中，无法低摩擦、零侵入地实时查阅当前 7K 铺面的 8 维技法雷达多边形（Technique Radar Profile）与双手时序应变曲线（Dual-Hand Strain Timeseries Profile）。

每次查看都需要手动切出游戏窗口或执行 CLI 命令，割裂了选歌决策与游玩体验；同时在 OBS 直播或录屏推流场景中，缺乏一个自包含、零外部网络依赖、支持透明背景的即时数据角标图层。

## Solution

遵循 [ADR-0010](file:///Users/kz/proj7k/docs/adr/0010-live-radar-profile-web-dashboard-and-log-watcher.md) 与 [`CONTEXT.md`](file:///Users/kz/proj7k/CONTEXT.md)，构建 `proj7k.live` 独立专用实时子系统：

1. **非侵入式实时铺面感知**：在后台非阻塞监听 osu!lazer 运行时日志（`runtime.log`），以毫秒级延迟精准捕获当前工作谱面变更；
2. **$O(1)$ 内存索引与极速缓存计算**：启动时预热加载 Realm 7K 谱面元数据，在内存中构建倒排寻址索引；配合双层缓存（`TwoLayerCache`），在选歌后 $<5\text{ms}$ 内完成特征抽取与雷达/应变向量装配；
3. **双工 WebSocket 广播**：基于原生 `asyncio` + `websockets` 向所有已连接的展示端推送标准化 JSON 状态帧；
4. **游玩时钟对齐与推测光标**：通过捕获游戏时钟点火与跳转事件（`GameplayClockContainer started` / `seeking to`），驱动前端游标以 1.0x 速率沿时序曲线高保真平滑滚动；
5. **纯原生自包含双模前端**：采用纯原生 HTML5 / Canvas / SVG 渲染，零外部 CDN 依赖，100% 离线可用；支持标准深色看板（`/`）与 OBS 透明角标（`/?mode=overlay`）。

## User Stories

1. 作为一名 7K 玩家，在 osu!lazer 选歌列表中切换谱面时，我希望第二显示器上的仪表盘能在瞬间自动更新该谱面的 8 维技法雷达图，以便我第一眼看清其核心技法倾向与短板分布。
2. 作为一名 7K 玩家，我希望在仪表盘顶部清晰看到谱面的 Intrinsic SR 星级、Jinjin Dan 段位层级（如 Zenith / Stellium）及主导技法标签（Dominant Technique），以便获得不受官方失衡算法误导的客观定级。
3. 作为一名 7K 玩家，我希望在时序应变曲线中直观区分左手与右手负荷（双色通道），以便评估单手疲劳度与手部独立性要求。
4. 作为一名 7K 玩家，我希望在时序应变图上看到 P90 权威难点线与 Top 5% 爆发致死区，以便快速定位全曲最具威胁性的致死段落。
5. 作为一名 7K 玩家，当我在 lazer 中开始游玩时，我希望应变曲线上有一条进度光标平滑推进，以便在打崩或感到卡手时实时知道对应曲目的哪一个时间切片。
6. 作为一名 7K 玩家，当我在游玩中重开（Restart）或按 Skip 跳过前奏时，我希望进度光标能立刻自动跳转对齐，而无需手动干预。
7. 作为一名音游主播，我希望在 OBS 中添加一个透明浏览器源（`/?mode=overlay`），以便在推流画面角标上只展示极简美观的雷达图和星级徽章。
8. 作为一名音游主播，我希望通过 URL 参数自由控制 Overlay 上的组件显隐（如隐藏时序图或调节缩放比例），以便完美融入我的个性化直播间排版。
9. 作为一名制谱者，我希望能够直接把尚未导入 lazer 的本地 `.osu` 文件拖拽到网页中，以便在制谱调试时立刻查看技法雷达变化。
10. 作为一名进阶算法研究者，我希望点击底部的指标抽屉展开 4D Tech 分量（紊乱度、剪切度、轨位熵、时基变异）与极速爆发数据，以便深入探究难度成因。
11. 作为一名在无网络/弱网环境下练图的玩家，我希望仪表盘在完全断网时依然能够 100% 正常启动和渲染，不产生任何第三方 CDN 报错。
12. 作为一名开发者，我希望通过一行清晰独立的命令（`python3 -m proj7k.live`）即可启动服务并自动在默认浏览器中打开页面。
13. 作为一名开发者，在 lazer 客户端未启动或关闭时，我希望服务能优雅降级等待，并允许纯手动拖拽分析，而不发生崩溃或抛出无意义异常。
14. 作为一名开发者，在遇到日志格式不匹配的自制谱面时，我希望系统提供基于标题与难度的模糊回退匹配，最大化匹配成功率。
15. 作为一名系统管理员，我希望 `proj7k.live` 作为一个纯只读服务运行，绝不持有或争抢 `client.realm` 的独占写锁，从而与 `proj7k.sync` 守护进程和谐共存。

## Implementation Decisions

### 模块划分与架构

1. **实时协调器核心 (Live Session Coordinator)**：
   - 作为系统顶层状态总线，协调日志监听器、Realm 内存索引映射器、特征计算引擎与 WebSocket 广播中心。
   - 维护当前的活动谱面状态（Active Beatmap State）与游玩时钟状态（Clock State）。

2. **运行时日志监听器 (Lazer Log Watcher)**：
   - 基于后台异步线程 / 协程非阻塞追踪 `~/Library/Application Support/osu/logs/*.runtime.log` 的文件尾部（tail）。
   - 自动识别最新生成的运行日志文件，处理日志轮转。
   - 正则提取两类核心事件：
     - 谱面切换事件：`Game-wide working beatmap updated to <Artist> - <Title> [<Difficulty>] (<Creator>)`；
     - 游戏时钟事件：`GameplayClockContainer started via call to StartGameplayClock` 以及 `GameplayClockContainer seeking to <ms>`。

3. **Realm 内存预热索引 (In-Memory Realm Index)**：
   - 服务启动时通过 Node 伴侣驱动（`bridge.js dump-7k`）读取 7K 谱面基础属性。
   - 在内存中构建规范化双向哈希表：`(clean_title, clean_diff) -> BeatmapRecord`，清洗剥离可能已注入的 `(X.XX★ Tech)` 后缀。
   - 提供基于 Levenshtein 或分词包含的模糊回退查找。
   - 直接按哈希分片快速定位内容寻址文件 `files/` 中的实体 `.osu` 文件。

4. **双通道数据协议契约 (WebSocket JSON Contract)**：
   - 客户端建立连接时，立即补发当前最新状态快照；
   - 铺面更新帧（`type: "beatmap_update"`）：包含曲目元数据、8 维雷达字典、综合星级、段位定位、左右手连续应变时序采样点数组与 P90/Top5% 阈值；
   - 时钟同步帧（`type: "clock_sync"`）：包含状态（`idle` / `playing`）、基准时间戳 `start_ms` 与游玩速率 `rate`；
   - 手动分析接口（`POST /api/analyze`）：支持接收上传的 `.osu` 文件内容并返回分析结果帧。

5. **纯原生无依赖前端资产 (Vanilla Frontend Assets)**：
   - 单一自包含 HTML 页面结构，内置响应式 CSS 与纯原生 JavaScript；
   - 使用 HTML5 2D Canvas 绘制 8 轴正多边形蛛网雷达图（支持外凸渐变填充、同心星级刻度环与 Dominant 高亮光晕）；
   - 使用 HTML5 2D Canvas 绘制双通道时序应变曲线（双通道曲线平滑插值、虚线阈值基准线与高帧率时间轴游标）；
   - 解析 URL 查询参数（`mode=overlay`, `radar`, `strain`, `scale`）自动切换 CSS 样式。

6. **CLI 独立入口 (Dedicated CLI Entrypoint)**：
   - 模块化入口 `src/proj7k/live/cli.py`（映射至 `python3 -m proj7k.live`）；
   - 提供 `--port`（默认 7770）、`--host`（默认 127.0.0.1）、`--open`（启动自动拉起浏览器）、`--no-watch`（禁用日志仅保留手动模式）、`--lazer-dir` 等参数。

## Testing Decisions

### 良好测试的定义

测试必须且仅验证系统的外部行为与对外协议，坚决不依赖内部实现细节与私有变量：

1. **最高核心集成接缝 (Highest Integration Seam) —— `LiveSessionCoordinator`**：
   - 模拟构造日志事件序列，验证经过索引解析、缓存计算后，WebSocket 广播通道对外输出的数据帧严格满足 8 维雷达与时序规范。
   - 验证选歌切换时的并发抗抖动能力，以及二次切歌时缓存命中的极速响应（$<5\text{ms}$）。
2. **自包含与离线防御接缝 (Zero-Dependency Offline Seam) —— `StaticAssetGuard`**：
   - 通过 HTTP 测试客户端遍历所有前端静态产物，断言其内容严禁包含任何外部第三方 HTTP/HTTPS CDN 引用，确保 100% 离线可靠。
3. **模糊回退匹配测试**：
   - 测试包含特殊字符、注入后缀及变体难度的日志行能正确映射到 Realm 记录。

### 现有测试先例 (Prior Art)

- `tests/test_lazer_sync_e2e.py`：端到端跨进程与文件寻址测试模式；
- `tests/test_radar.py`：8 维雷达与正交抑制数学正确性检验；
- `tests/test_rating.py`：星级综合与软上限平滑性验证。

## Out of Scope

1. **跨进程侵入式内存读取**：不使用任何基于内存特征码的外部工具（如 tosu / gosumemory），不进行进程内内存注入。
2. **谱面物理文件与数据库改写**：本实时服务为 100% 只读系统，不执行任何写事务与数据库注入（写事务属于 `proj7k.sync` 职责）。
3. **浏览器端音频播放与波形解码**：前端仅展示时序应变曲线与时钟游标，不承担音频流解码与播放器功能。

## Further Notes

- 严格遵循 [ADR-0010](file:///Users/kz/proj7k/docs/adr/0010-live-radar-profile-web-dashboard-and-log-watcher.md)；
- 架构领域术语与定义与 [`CONTEXT.md`](file:///Users/kz/proj7k/CONTEXT.md) 保持强一致；
- 作为主图工单 [#2](https://github.com/ka1z07/proj7k/issues/2) 的 Phase 2.4 子里程碑交付物。
