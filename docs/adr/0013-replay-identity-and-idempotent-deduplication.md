# 0013. 回放身份指纹与幂等去重 (Replay Identity & Idempotent Deduplication)

- **状态**：accepted
- **日期**：2026-09-21
- **背景**：
  [ADR-0012](0012-player-profiler-and-closed-loop-training.md) 第 7 条确立了本地轻量 SQLite 档案库（`~/.proj7k/profiler.db`）与多身份隔离机制，但**未定义单次对局的身份标识**。其结果是历史摄入路径以普通 `INSERT` 写入且无任何唯一性约束，同一目录重复执行批量摄入即产生重复行，破坏滚动近期状态（Recent Rolling Form）与历史巅峰画像（All-time Peak Profile）的聚合基准。
  与此同时，新增的**历史回放导入**通道（`client.realm` → SQLite，复用 [ADR-0009](0009-lazer-realm-non-destructive-ingestion-and-node-bridge.md) 的 Node Realm Bridge）需要一个跨导出格式与跨批次的稳定身份键，方能实现幂等重跑。
  本决策补全 ADR-0012 遗留的身份语义，并显式记录新增的持久化约束与其迁移代价。

## 决策内容

1. **回放指纹作为唯一身份键 (Replay Fingerprint as Identity Key)**：
   - **导入通道**：以 osu!lazer `files/` 存储中的物理回放文件哈希作为回放指纹，保证同一物理回放跨 `.osr` 导出格式与跨批次解析完全一致；
   - **批量通道**：以 `.osr` 头部的 replay MD5 为回放指纹，缺失时回退至文件名 stem；
   - 回放指纹是**对局身份**的唯一权威，不采用时间戳、文件名或玩家名组合等近似判据。

2. **幂等摄入与冲突定向吸收 (Idempotent Ingestion with Targeted Conflict Absorption)**：
   - `match_snapshots.replay_hash` 上建立**唯一索引**（`idx_replay_hash`），由该显式索引独占承担身份约束，不再叠加列级 `UNIQUE`（列级约束经 `CREATE TABLE IF NOT EXISTS` 永远无法到达既有档案库，在新建档案库上又会与索引重复）；
   - 写入采用 `ON CONFLICT (replay_hash) DO NOTHING`，**明确拒绝 `INSERT OR IGNORE`**：后者会一并吞没 NOT NULL 与 CHECK 约束冲突，使畸形快照被静默丢弃并被上层误报为“已存在”或“噪声过滤”，属于持久化层的静默数据丢失反模式。

3. **遗留档案库自动去重 (Legacy Database Auto-Dedup)**：
   - 打开档案库时，在建立唯一索引之前按回放指纹保留最早一行、删除其余重复行并记录告警；
   - 该步骤不可省略：既有档案库中已存在重复指纹时，唯一索引创建将抛出 `IntegrityError`，而索引创建位于 `ProfilerStorage` 构造路径上，会导致**所有依赖档案库的指令一并不可用**且无修复入口。

4. **玩家身份归一与表达式索引 (Player Identity Normalization & Expression Index)**：
   - 玩家标识在持久化与检索两侧统一执行空白裁剪，并以大小写折叠实现分档隔离；
   - `player_name` 上的复合索引以 `LOWER(player_name)` **表达式**建立，使大小写不敏感过滤与 `ORDER BY timestamp DESC` 均由同一索引服务；
   - 已知边界：SQLite 的 `LOWER()` 仅折叠 ASCII，而 osu! 用户名限定为 ASCII 字符集，故此约束在本领域内完备。

5. **单向导入的命名边界 (Inbound Import Naming Boundary)**：
   - 档案库方向（`client.realm` → SQLite）的导入开关命名为 `--import-replays`；
   - **不得复用 `--sync-lazer`**：该标志已由 [ADR-0011](0011-closed-loop-strain-downscaler-and-technique-preservation.md) 赋予**反向**语义（将衍生练习谱面注入 `7K Practice` 收藏夹），同名反向复用会破坏 CLI 语义的单一性。

## 权衡与取舍 (Trade-offs)

- **为什么以物理回放文件哈希而非时间戳作为身份键**：
  时间戳组合判据在重导出、跨批次重跑与时钟回退下均不幂等，且两个真实对局可能落入同一时间窗口；物理回放哈希是对局内容的客观指纹，使重跑天然收敛且无需额外的近似匹配阈值。
- **为什么遗留档案库选择自动去重而非拒绝启动**：
  拒绝启动把迁移负担转嫁给用户，且档案库位于用户主目录、缺少显式迁移入口，用户几乎无从修复。自动去重保留最早样本（即首次摄入的原始观测），仅移除冗余副本，不损失任何独立观测；代价是打开档案库时存在一次隐式写入，故必须以告警显式披露删除行数。
- **为什么坚持表达式索引而不是在查询侧退化**：
  以 `LOWER(player_name) = LOWER(?)` 过滤但仅索引裸列，会使查询计划由索引 `SEARCH` 降级为全表 `SCAN`，并额外引入临时 B 树承担排序。随着对局累积，档案库查询将随历史线性劣化，与本决策追求的毫秒级时序窗口聚合目标直接冲突。
