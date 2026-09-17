# 0010. 实时 8 维技法雷达与时序应变仪表盘架构 (Live Radar Dashboard)

- **状态**：accepted
- **日期**：2026-09-17
- **背景**：
  在 Phase 2.2 与 Phase 2.3 完成 8 维能力雷达标定算法与 osu!lazer 数据库无损注入后，系统需要进一步提供直观、即时、低摩擦的实时可视化载体。玩家在选歌、制谱或实机游玩过程中，期望在第二显示器或 OBS 直播推流画面中实时查阅当前 7K 铺面的 8 维技法雷达拓扑（Technique Radar Profile）与双手时序应变负荷（Strain Timeseries Profile）。

## 决策内容

1. **信息架构与领域范围 (Information Architecture)**：
   - **8 维技法雷达 (Technique Radar)**：展示 Regular 轨（Jack, Tech, Speed, Stream）与 LN 轨（LN General, LN Tech, LN Inverse, LN Release）的 0~12.5★ 多边形拓扑，高亮主导技法（Dominant Technique）与 Intrinsic SR 综合评级，并锚定 Jinjin 7K Dan 权威段位层级（如 Zenith / Stellium）。
   - **双手时序应变剖面 (Dual-Hand Strain Profile)**：展示基于连续衰减累积模型计算的全曲时序曲线，以双通道色区分左手负荷（$S_{\text{left}}(t)$）与右手负荷（$S_{\text{right}}(t)$），显式标定 P90 难点基准线与 Top 5% 爆发致死区。
   - **细分特征抽屉**：可折叠展示 4D Tech 技巧分量（Tortuosity, Bracket Shear, Spatial Entropy, Rhythmic Irregularity）与微观极速爆发等底层指标。

2. **多模自适应呈现与 OBS 图层适配 (Dual-Mode Presentation)**：
   - **默认仪表盘模式 (`/`)**：深色赛博电竞风格，标准三栏式布局，包含完整元数据横幅、雷达图、时序应变图、细分抽屉与拖拽分析区。
   - **OBS Overlay 模式 (`/?mode=overlay`)**：透明背景、无滚动条、紧凑 HUD 挂件，支持 Query 参数微调组件显隐与缩放（如 `?mode=overlay&radar=1&strain=1&scale=1.0`），专为直播与录屏画面角标优化。

3. **双通道非侵入式感知与时钟对齐机制 (Real-time Watcher)**：
   - **运行时日志监听**：后台线程非阻塞监听 `~/Library/Application Support/osu/logs/*.runtime.log`，捕获 `Game-wide working beatmap updated to ...` 事件。
   - **Realm 内存预热索引**：服务启动时通过 Node 伴侣桥接预热 Realm 7K 谱面列表（~0.29s 载入 9,700+ 谱面），在内存中建立正则清洗后的 `(title, diff) -> record` 索引，实现选歌到实体 `.osu` 文件的 $O(1)$ 寻址（<5ms 延迟）。
   - **游玩时钟对齐**：监听 `GameplayClockContainer started via call to StartGameplayClock` 与 `seeking to <ms>` 事件，驱动前端游标以 1.0x 速率沿应变曲线精确推进，切歌或重开自动复位。
   - **离线拖拽兜底**：前端提供拖拽上传与文件路径直接解析通道，兼顾本地单图调试。

4. **全自包含与零外部依赖保障 (Zero-Dependency & Offline First)**：
   - 前端采用纯原生 HTML5 / Canvas / SVG 绘制蜘蛛网多边形与动态时序曲线，不依赖任何外部 CDN 图表库，体积 <30KB。
   - 后端基于 Python 标准库 `http.server`、`asyncio` 与已安装的 `websockets` 库构建轻量 WebSocket 双工推送服务。
   - 100% 保障断网、弱网及内网局域网环境下的可靠运行。

5. **独立专用 CLI 契约 (CLI Entrypoint)**：
   - 提供独立入口 `python3 -m proj7k.live`，支持 `--port`、`--host`、`--open`、`--no-watch` 等参数，与负责写事务的 `proj7k.sync` 严格解耦。

## 权衡与取舍 (Trade-offs)

- **为什么不采用进程内存注入或第三方内存读取器 (如 tosu / gosumemory)**：
  macOS 平台对于跨进程内存读取具有严格的 SIP / 权限沙箱限制，且随着 osu!lazer 频繁更新，内存特征码极易失效。监听 lazer 原生运行时日志与 Realm 内存缓存是零依赖、永久稳定且受官方日志机制支持的最佳非侵入式通道。
- **为什么不使用外部 CDN (如 Chart.js / ECharts)**：
  音游玩家常在离线或多网络切换状态下游玩，外部 CDN 存在潜在加载延迟与不可用风险；纯原生 Canvas 绘制雷达与时序图性能极高，内存与 CPU 占用几乎为零。
- **为什么与 `proj7k.sync` 解耦**：
  `proj7k.sync` 面向数据库批量事务与独占文件锁（关注原子写入与容灾），而 `proj7k.live` 是长驻常开的只读展示与推流服务，分立可避免任何写锁竞争与生命周期耦合。
