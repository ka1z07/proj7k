# Ticket 6: 安全刷盘窗探针、容灾备份与守护 CLI 总控

- **ID**: `SPEC-P2.3-03`
- **状态**: `closed`
- **标签**: `ready-for-human`
- **阻塞依赖 (Blocked by)**: `SPEC-P2.3-02`
- **所属父规格**: [`docs/specs/phase2.3-lazer-realm-ingestion-and-sync-daemon.md`](file:///Users/kz/proj7k/docs/specs/phase2.3-lazer-realm-ingestion-and-sync-daemon.md)
- **关联 ADR**: [`docs/adr/0009-lazer-realm-non-destructive-ingestion-and-node-bridge.md`](file:///Users/kz/proj7k/docs/adr/0009-lazer-realm-non-destructive-ingestion-and-node-bridge.md)

---

## 任务背景

为了安全落地整个注入系统，需要最后一公里闭环：通过 POSIX 锁无损嗅探 osu!lazer 游戏进程的锁状态，在安全刷盘窗（Safe Flush Window）期间驱动增量快照计算与落盘，管理滚动快照备份并提供一键 `--revert` 恢复机制，最后通过统一 CLI 工具（`python3 -m proj7k.sync`）交付。

---

## 实施范围 (Scope)

1. **POSIX 锁探针 (`src/proj7k/lazer/lock.py`)**：
   - 使用 `fcntl.flock` 对 `client.realm.lock` 进行非阻塞排他锁测试；
   - 若能获得锁，判定游戏未运行（安全刷盘窗已开启）；若捕获 `BlockingIOError` / `EAGAIN`，判定游戏正在运行。
2. **容灾快照与状态恢复 (`src/proj7k/lazer/backup.py`)**：
   - 自动生成 `client.realm.backup_<timestamp>` 轮转快照（默认保留最近 3 份）；
   - 维护 `.cache/proj7k/lazer_backup_state.json` 记录所有被改写的原始 `BeatmapInfo` 属性；
   - 实现 `revert_realm_modifications()`，无损恢复全部原始元数据。
3. **快照 Diff 与增量跑批引擎 (`src/proj7k/lazer/daemon.py`)**：
   - 结合 `proj7k.TwoLayerCache`，对比 Realm 导出的 7K 谱面列表与已计算哈希；
   - 仅对增量新图调用 `evaluate_intrinsic_difficulty`；
   - 生成更新字典并交由 `bridge.py` 写入。
4. **统一 CLI 主入口 (`python3 -m proj7k.sync`)**：
   - 提供子命令与选项：
     - `python3 -m proj7k.sync --once`：立即单次增量同步（若遇游戏运行则报错或等待）；
     - `python3 -m proj7k.sync --daemon`：作为守护进程常驻，监听安全窗口并自动静默同步；
     - `python3 -m proj7k.sync --revert`：一键完全回滚所有修改；
     - `python3 -m proj7k.sync --setup`：初始化 Node.js 驱动依赖。
5. **全链路端到端集成测试 (`tests/test_lazer_sync_e2e.py`)**。

---

## 验收条件 (Acceptance Criteria)

- [x] `lock.py` 在并发模拟测试中 100% 正确判定游戏锁状态；
- [x] 备份与回滚机制通过无损全量回滚断言；
- [x] CLI 入口完整支持 `--once`, `--daemon`, `--revert`, `--setup`；
- [x] 现有测试全量通过无回归，覆盖率维持在 90% 以上（实际达到 98%）。

