# Ticket 9: 双手时序应变曲线组装与游戏时钟精准对齐

- **Issue**: [#24](https://github.com/ka1z07/proj7k/issues/24)
- **ID**: `SPEC-P2.4-03`
- **状态**: `open`
- **标签**: `ready-for-agent`
- **阻塞依赖 (Blocked by)**: [#23](https://github.com/ka1z07/proj7k/issues/23)
- **所属父规格**: [`docs/specs/phase2.4-live-radar-profile-and-stream-dashboard.md`](file:///Users/kz/proj7k/docs/specs/phase2.4-live-radar-profile-and-stream-dashboard.md)
- **关联 ADR**: [`docs/adr/0010-live-radar-profile-web-dashboard-and-log-watcher.md`](file:///Users/kz/proj7k/docs/adr/0010-live-radar-profile-web-dashboard-and-log-watcher.md)

---

## What to build

在分析引擎中接入底层 `compute_dual_hand_strain` 算法，生成全曲双手时序应变曲线（包含左手采样序列 `left_strains` 与右手采样序列 `right_strains`、时间轴 `sample_times_ms`，以及 P90 基准线和 Top 5% 爆发致死阈值），并打包入 WebSocket 状态帧。
扩展日志监听器，捕获 `GameplayClockContainer started via call to StartGameplayClock` 与 `GameplayClockContainer seeking to <ms>` 事件，向 WebSocket 客户端广播高精度时钟对齐帧（包含当前游玩状态、起始基准时间与速率），支持实机游玩、重开与跳过前奏时的精确同步。

---

## Acceptance criteria

- [ ] WebSocket 谱面数据帧中完整包含左手与右手连续时序应变数据及 P90、Top5% 阈值；
- [ ] 日志监听器能正确捕获时钟点火（Started）与跳转（Seeking）事件，并向客户端推送 `clock_sync` 消息；
- [ ] 在谱面未处于游玩状态时，正确标记时钟为空闲/未激活；
- [ ] 编写时序组装与时钟对齐事件流的自动化测试。
