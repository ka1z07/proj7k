# Ticket 10: 自包含 Canvas 交互式看板与 OBS 浏览器源自适应图层

- **Issue**: [#25](https://github.com/ka1z07/proj7k/issues/25)
- **ID**: `SPEC-P2.4-04`
- **状态**: `open`
- **标签**: `ready-for-agent`
- **阻塞依赖 (Blocked by)**: [#24](https://github.com/ka1z07/proj7k/issues/24)
- **所属父规格**: [`docs/specs/phase2.4-live-radar-profile-and-stream-dashboard.md`](file:///Users/kz/proj7k/docs/specs/phase2.4-live-radar-profile-and-stream-dashboard.md)
- **关联 ADR**: [`docs/adr/0010-live-radar-profile-web-dashboard-and-log-watcher.md`](file:///Users/kz/proj7k/docs/adr/0010-live-radar-profile-web-dashboard-and-log-watcher.md)

---

## What to build

构建纯原生自包含 HTML5/Canvas/CSS 前端产物，彻底消除外部 CDN 依赖（100% 离线可用）：
1. 默认仪表盘模式 (`/`)：三栏式深色电竞风布局，HTML5 Canvas 绘制 8 轴蜘蛛网雷达图（带同心星级环、高亮 Dominant 光晕）、双色时序应变曲线、平滑推进的 1.0x 游标、可折叠 4D Tech 细分抽屉（紊乱度、剪切度、轨位熵、时基变异）与本地文件拖拽解析区；
2. OBS Overlay 图层 (`/?mode=overlay`)：纯透明背景、紧凑 HUD 挂件、无滚动条，支持 URL 参数自定义显隐与缩放（如 `?mode=overlay&radar=1&strain=0&scale=1.0`）；
3. 自动化测试接入 `StaticAssetGuard`，遍历所有静态产物，断言其内容严禁包含任何外部第三方 HTTP/HTTPS CDN 引用。

---

## Acceptance criteria

- [ ] 页面在断网状态下（离线）能完美渲染雷达多边形与时序应变波形图；
- [ ] 游玩时钟光标能依据 `clock_sync` 消息以 1.0x 速率平滑滚动并在切歌/重开时精准复位；
- [ ] 访问 `/?mode=overlay` 时自动呈现透明背景与紧凑布局，并正确响应 URL query 参数；
- [ ] `StaticAssetGuard` 自动化测试 100% 通过，坚决杜绝外部 CDN 泄露；
- [ ] 整个系统端到端验证通过，已有全量测试全绿无回归。
