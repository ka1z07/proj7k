# Ticket 8: osu!lazer 运行时日志监听与 Realm 内存索引倒排寻址

- **Issue**: [#23](https://github.com/ka1z07/proj7k/issues/23)
- **ID**: `SPEC-P2.4-02`
- **状态**: `closed`
- **标签**: `ready-for-agent`
- **阻塞依赖 (Blocked by)**: [#22](https://github.com/ka1z07/proj7k/issues/22)
- **所属父规格**: [`docs/specs/phase2.4-live-radar-profile-and-stream-dashboard.md`](file:///Users/kz/proj7k/docs/specs/phase2.4-live-radar-profile-and-stream-dashboard.md)
- **关联 ADR**: [`docs/adr/0010-live-radar-profile-web-dashboard-and-log-watcher.md`](file:///Users/kz/proj7k/docs/adr/0010-live-radar-profile-web-dashboard-and-log-watcher.md)
- **解决 Commit**: [`7f4bebd`](https://github.com/ka1z07/proj7k/commit/7f4bebd), [`880e21b`](https://github.com/ka1z07/proj7k/commit/880e21b)

---

## What to build

实现 `LazerLogWatcher` 后台协程/线程，非阻塞监听 `~/Library/Application Support/osu/logs/*.runtime.log` 中的工作谱面切换事件（`Game-wide working beatmap updated to ...`）。
服务启动时通过 Node 伴侣驱动桥接（`bridge.js dump-7k`）预热全量 7K 谱面元数据，在内存中构建正则清洗后的 `(clean_title, clean_diff) -> BeatmapRecord` 倒排寻址字典，并支持模糊回退匹配。
在捕获选歌事件后，瞬间（<5ms）定位 `files/` 中的实体 `.osu` 文件，调用分析引擎计算雷达数据并通过 Ticket 1 的 WebSocket 管道向客户端广播。

---

## Acceptance criteria

- [x] `LazerLogWatcher` 能在后台平稳 tail 最新 `runtime.log`，遇到日志轮转时自适应切换；
- [x] 准确识别提取日志中的 Artist, Title, DifficultyName 与 Creator；
- [x] 内存索引能在启动时在 0.5s 内构建完成，并在清洗注入后缀（如 `(10.74★ Tech)`）后实现 O(1) 字典寻址；
- [x] 无法精确匹配时，模糊匹配能有效回退，并支持文件不存在时的优雅错误通知；
- [x] 编写端到端日志事件注入模拟测试，验证选歌触发完整的 WebSocket 广播。
