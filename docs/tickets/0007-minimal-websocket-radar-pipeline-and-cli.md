# Ticket 7: 最小端到端 WebSocket 实时雷达推流管道与 CLI 骨架

- **Issue**: [#22](https://github.com/ka1z07/proj7k/issues/22)
- **ID**: `SPEC-P2.4-01`
- **状态**: `open`
- **标签**: `ready-for-agent`
- **阻塞依赖 (Blocked by)**: None (首顺位任务)
- **所属父规格**: [`docs/specs/phase2.4-live-radar-profile-and-stream-dashboard.md`](file:///Users/kz/proj7k/docs/specs/phase2.4-live-radar-profile-and-stream-dashboard.md)
- **关联 ADR**: [`docs/adr/0010-live-radar-profile-web-dashboard-and-log-watcher.md`](file:///Users/kz/proj7k/docs/adr/0010-live-radar-profile-web-dashboard-and-log-watcher.md)

---

## What to build

提供独立的 `python3 -m proj7k.live` CLI 入口与轻量异步服务（基于 Python 原生 `asyncio` + `websockets` + `http.server`）。
支持客户端通过 WebSocket (`/ws`) 连接并接收广播；提供手动文件路径或上传接口，调用底层 `evaluate_intrinsic_difficulty` 与 `TwoLayerCache` 计算 8 维能力雷达（Jack, Tech, Speed, Stream, LN General, LN Tech, LN Inverse, LN Release）与 Intrinsic SR，并输出规范化的 JSON 状态帧。
内置一个最小 HTTP 静态页，可在浏览器中连接并验证接收到的雷达数据。

---

## Acceptance criteria

- [ ] 运行 `python3 -m proj7k.live --no-watch` 能成功在指定端口（默认 7770）拉起 HTTP 和 WebSocket 服务；
- [ ] WebSocket 客户端连接后能收到欢迎状态帧，并能发送手动分析请求；
- [ ] 分析引擎调用 `evaluate_intrinsic_difficulty` 与 `TwoLayerCache`，输出符合 `TechniqueRadar` 与 `StarRatingSynthesis` 的完整 JSON 字典；
- [ ] 编写端到端单元测试与异步模拟测试，验证 WebSocket 发送与接收行为正常。
