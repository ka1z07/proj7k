# Ticket 4: Node.js Realm 伴侣驱动与 Python 管道通信契约

- **ID**: `SPEC-P2.3-01`
- **状态**: `closed`
- **标签**: `ready-for-agent`
- **阻塞依赖 (Blocked by)**: 无（首顺位任务）
- **所属父规格**: [`docs/specs/phase2.3-lazer-realm-ingestion-and-sync-daemon.md`](file:///Users/kz/proj7k/docs/specs/phase2.3-lazer-realm-ingestion-and-sync-daemon.md)
- **关联 ADR**: [`docs/adr/0009-lazer-realm-non-destructive-ingestion-and-node-bridge.md`](file:///Users/kz/proj7k/docs/adr/0009-lazer-realm-non-destructive-ingestion-and-node-bridge.md)

---

## 任务背景

`proj7k` 核心评估引擎全量使用 Python 编写，但 Realm 官方缺乏受支持的现代 Python 驱动。为操作 osu!lazer 的 `client.realm`，需借助宿主机原生 Node.js（`/opt/homebrew/bin/node`）运行官方 `realm` 驱动，通过子进程 stdio JSON 管道与 Python 守护总控交互。

---

## 实施范围 (Scope)

1. **搭建 Node.js 驱动伴侣 (`tools/lazer-bridge/`)**：
   - 创建 `package.json`（依赖 `realm`）；
   - 实现 `bridge.js`，支持如下子命令/协议：
     - `dump-7k`：读取 `client.realm` 中所有 `Ruleset.OnlineID == 3 && Difficulty.CircleSize == 7` 的谱面元数据（ID, Hash, StarRating, DifficultyName, Tags, FileHash）；
     - `update-batch`：在单一 Realm 写事务中原子更新传入谱面的 `StarRating`、`DifficultyName`、`Tags`，并同步更新/创建 `BeatmapCollection`；
     - `revert-batch`：基于传入的原始字典将指定谱面回滚为原始状态。
2. **构建 Python 客户端通道 (`src/proj7k/lazer/bridge.py`)**：
   - 实现 `RealmBridgeClient` 类；
   - 自动检测 Node.js 运行时与 `realm` npm 模块就绪状态；
   - 提供自动 `ensure_installed()` 静默运行 `npm install`；
   - 封装 `dump_7k_beatmaps()` 与 `apply_batch_update()` 等强类型 API。
3. **编写单元与契约测试**：
   - 在 `tests/test_lazer_bridge.py` 中编写命令拼装、协议序列化与异常回退测试。

---

## 验收条件 (Acceptance Criteria)

- [x] `tools/lazer-bridge/bridge.js` 能在 node 环境下正确解析并响应 JSON 协议输入；
- [x] `RealmBridgeClient` 能自动感知 node 路径与依赖缺失并支持自动 setup；
- [x] 针对 Mock/临时 Realm 文件或协议 Mock 的单元测试 100% 通过（8 项测试全绿）；
- [x] 全量已有测试（133 passed）无任何回归。

