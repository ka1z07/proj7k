# 0009. osu!lazer 数据库无损注入与 Node.js 驱动桥接架构

- **状态**：accepted
- **日期**：2026-09-16
- **背景**：
  在完成 Phase 2.2 铺面固有难度（Intrinsic SR）与 8 维能力雷达算法后，需要将该评估系统无缝接入游戏本体，使用户能在日常选歌与游玩中直接体验并验证客观难度评级。用户操作系统为 macOS，活跃使用 osu!lazer 客户端（本地维护有基于 Realm 引擎的 `client.realm` 及 39,000+ 文件的内容寻址存储库）。

## 决策内容

1. **无损数据库级注入 (Non-Destructive Ingestion)**：
   - 彻底摒弃改写 `.osu` 物理文本文件的侵入式方案，严格保持铺面物理文件内容及其 SHA-256 / MD5 哈希不变，杜绝破坏本地与官方联机排行榜的成绩提交校验。
   - 直接介入 osu!lazer 的本地数据库 `client.realm`，将目标 7K 谱面（`Ruleset.OnlineID == 3 && Difficulty.CircleSize == 7`）的 `BeatmapInfo.StarRating` 原地改写为 `proj7k` 合成的 Intrinsic SR。

2. **游戏内交互与呈现三位一体 (Biaxial Presentation Contract)**：
   - **难度名直观标注**：在 `BeatmapInfo.DifficultyName` 追加后缀 `{original} ({SR:.2f}★ {Dan} {Dominant})`（如 `Hard (6.42★ 7th Jack)`），并在更新时利用正则表达式 `\s*\(\d+\.\d+★(?:\s+[A-Za-z0-9_]+){1,2}\)$` 实现历史旧格式与新格式的双向幂等清洗替换。_（后缀契约已由 [ADR-0014](0014-injection-methodology-version-and-reevaluation.md) 扩展：追加方法学版本令牌，并以该文所载格式与正则为准。）_
   - **离散星级桶与段位标签注入**：在 `BeatmapMetadata.Tags` 注入 `dominant_{tech}`、离散化星级桶（如 `jack_6★`）以及所属段位标签 `dan_{tier}`（如 `dan_7th`、`dan_gamma`），绕过 osu!lazer 搜索解析器无法解析自定义属性动态数值比较（如 `jack>6.0`）的局限，实现搜索栏文本秒搜（可直接通过 `dan_7th` 精准过滤对应段位铺面）。
   - **双轴矩阵收藏夹**：在 `BeatmapCollection` 中自动同步 8 大技法专项收藏夹（`7K Jack`、`7K Tech` 等）与 4 档段位阶梯收藏夹（`7K Dan 00th-03rd` 等），共计 12 个聚合收藏夹。

3. **安全刷盘窗与并发互斥防护 (Safe Flush Window)**：
   - 监听 `client.realm.lock` 文件的 POSIX 独占锁状态。
   - 守护进程在游戏运行时在后台只读或利用双层缓存（TwoLayerCache）预热计算；仅在检测到游戏退出或文件锁释放的瞬间缝隙（Safe Flush Window）触发批量数据库事务写入，100% 杜绝数据库跨进程读写锁冲突与损坏风险。

4. **Python 调度与 Node.js Realm 伴侣驱动桥接 (Python-Node Driver Bridge)**：
   - `proj7k` 核心算法与特征抽取全量保留在 Python 3.14 环境。
   - 鉴于 Realm 官方缺乏受支持的现代 Python SDK，在 `tools/lazer-bridge/` 构建微型 Node.js 驱动脚本（依赖官方 `@realm/core` / `realm` npm 包），通过子进程标准输入输出（stdio）JSON 管道执行原子级 Realm 事务。
   - 提供自动静默初始化机制，首次运行时自动检测并补齐 npm 依赖。

5. **容灾备份与一键无损回滚 (Disaster Recovery & Reversion)**：
   - 守护进程在每次批量落盘前自动生成轮转滚动快照（`client.realm.backup_<timestamp>`，保留最近 3 份）。
   - 维护 `.cache/proj7k/lazer_backup_state.json` 记录每首谱面被修改前的原始 `StarRating`、`DifficultyName` 与 `Tags`。
   - 提供 `proj7k-daemon --revert` 指令，支持随时随地一键还原为官方纯净状态。

## 权衡与取舍 (Considered Options & Trade-offs)

- **拒绝改写 `.osu` 物理文件**：改写难度名或标签会导致文件哈希改变，使谱面被 lazer 判定为本地脏副本而无法联机匹配与提交成绩。数据库无损注入是唯一能兼顾游戏内视觉与官方联机合规的途径。
- **拒绝用 C# 重写算法或自制 Ruleset Mod**：移植 13 维拓扑特征、非线性门控与广义范数极其容易引入浮点与语义偏差，且 Ruleset Mod 无法直接对官方 Mania 模式曲目库生效。Python 算法总控 + Node.js Realm 桥接以最小代价撬动了成熟资产。
- **拒绝游戏运行期侵入式抢锁写入**：Realm 的进程锁是强约束，强行争抢可能导致游戏主线程卡顿甚至崩溃。利用安全刷盘窗将写入收敛到退出或空闲时刻，是用 0 运行风险换取秒级延迟的最佳平衡点。
