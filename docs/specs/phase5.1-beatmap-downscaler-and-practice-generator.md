# Spec: 7K 铺面闭环时序应变降阶器与自适应练习引擎 (Phase 5.1)

## Problem Statement

7K 高难铺面（如 Jinjin 10th Dan、Gamma、Stellium、200+ BPM Chordjack 或密集反键 LN）常常包含微观极速爆发、原位连续停滞（$\Delta k=1$）以及高负空间认知阻抗，导致进阶阶段的玩家在遭遇致死峰值（Burst Peak）或长程腱力枯竭时瞬间暴毙，无法获得有效的肌肉记忆与读谱训练。

当前玩家普遍采用的降级手段存在严重缺陷：
1. **音频播放变速（如 0.8x / 0.9x Rate）**：强行改变音乐原速与判定时钟窗口，削弱了玩家在原速音乐律动下的打击体感，且无法化解特定高密图元对单手自由度的压制；
2. **粗暴的随机/步长抽稀脚本**：完全脱离音乐节拍网格与键形拓扑，导致小节强拍被突兀抹成大空白，甚至引起技法本质漂移（例如将高密度弦叠 Chordjack 抽稀成了散乱切键 Stream，完全丧失了专项针对性训练价值）；
3. **缺乏与客户端集成的自动化闭环**：玩家需要反复手动导入导出 `.osu` 文件、手动计算难度、手动建立收藏夹，操作摩擦极大。

系统需要一套客观第一性原理驱动的**铺面降阶器 (Beatmap Downscaler)**，能以目标段位为锚点，闭环收敛微观键力与认知应变，在严格保全音乐骨架、双手生理平衡与专项技法主导性的前提下，一键生成高质量的**衍生练习谱面 (Derivative Practice Beatmap)**。

## Solution

遵循 [ADR-0011](file:///Users/kz/proj7k/docs/adr/0011-closed-loop-strain-downscaler-and-technique-preservation.md) 与 [`CONTEXT.md`](file:///Users/kz/proj7k/CONTEXT.md)，构建 `proj7k.downscaler` 核心降阶引擎与本地工具链：

1. **双层级联段位映射 (Two-Tier Dan Cascade Mapping)**：以 Jinjin 7K Dan 段位（`--target-dan`）为核心输入，第一层优先检索 120 首标杆曲目蒸馏特征库（`distilled_ground_truth.json`）提取该专项的段位质心应变 $S_{\text{target}}$ 与雷达轮廓；第二层以 15 级权威段位与固有星级标尺进行平滑插值。
2. **闭环峰值时间窗分批剪枝 (Windowed Peak-Batch Pruning)**：以 `proj7k.strain` 双手时序应变模型为闭环适应度函数，自动定位所有超过 $S_{\text{target}}$ 的局部超标峰值区间，结合双手动态通量失衡惩罚，按边际贡献对非骨架音符批量修剪，实现数秒内快速数学收敛。
3. **纯音符剔除变异 (Pure Deletion Mutation)**：变异原子动作限定为米键直接删除、长条头身尾一体完整删除，确保衍生谱面图元闭集纯正、单调递减物理键力，杜绝任何人工截断的畸形图元。
4. **复合骨架与双手平衡刚性约束 (Metric & Biomechanical Invariants)**：小节 1/1 强拍至少保留 1 音底座；多押和弦只削减押数，杜绝整拍全删为空轨；引入左右手打击通量平衡反馈乘子，自适应抑制单手过度削减引发的生理代偿。
5. **双轨技法守恒门禁 (Dual-Gate Technique Preservation)**：降阶前后 8 维技法雷达向量方向余弦相似度 $\cos(\hat{\vec{R}}_{\text{nerfed}}, \hat{\vec{R}}_{\text{orig}}) \ge 0.80$，且原谱第 1 顺位主导技法严格守恒，主导特征质心位于置信区间内。
6. **全息追溯元数据与 Lazer Bridge 联动**：在原谱同目录下生成 `[P-{TargetDan} {DominantSkill}] {OriginalVersion}` 衍生难度，注入包含 `orig_md5` 的全息追溯 Tags，支持通过 `--sync-lazer` 安全刷盘无损注册进 osu!lazer 的 `7K Practice` 专属收藏夹。

## User Stories

1. 作为一名正在备考 7th Dan 的 7K 玩家，我希望输入一张 10th Dan 的高难 Chordjack 谱面并指定 `--target-dan 7th`，系统能将其精准降阶为 7 段难度的衍生练习谱，以便我能在原速音乐下进行高强度的腱力进阶练习。
2. 作为一名 7K 玩家，我希望降阶生成的练习谱面严格保留每一小节的第一拍（Measure Downbeat）强拍鼓点与主要重音，以便敲击时依然能够紧扣音乐旋律的主干律动，而不产生任何突兀的静音空档。
3. 作为一名 7K 玩家，我希望当原图出现并发多押（如 3 押、4 押）时，降阶器只削减其和弦押数（如 4 押降为 2 押），而绝不把整个节拍的所有按键全删为空轨，以便维持击键节奏的连续性。
4. 作为一名 7K 玩家，我希望降阶一张 Chordjack 谱面后，其主导技法依然被严格判定为 Chordjack，并且雷达轮廓相似度 $\ge 0.80$，以便我的练习精力准确聚焦在和弦震颤与同轨停滞（$\Delta k=1$）控制上，而不是退化为普通的散打切键。
5. 作为一名 7K 玩家，我希望降阶一张 LN 混押谱面后，其关键的长条持握骨架依然保留，以便持续训练手指的独立性与自由度压制抗性。
6. 作为一名 7K 玩家，我希望降阶算法在削减音符时能够动态平衡左手与右手的打击通量比，避免因为单手过度削减导致另一只手持续超负荷而产生生理肌肉代偿。
7. 作为一名 7K 玩家，我希望在游戏内选歌界面能一眼看清练习谱面的目标段位与主导技法（例如难度名为 `[P-7th Jack] Insane`），以便我能够迅速挑选出适合当前热身或专项突破的曲目。
8. 作为一名 7K 玩家，我希望指定 `--sync-lazer` 参数后，降阶生成的练习谱面能够自动导入本地 osu!lazer，并打上离散星级桶标签加入 `7K Practice` 游戏内收藏夹，无需任何手动文件复制或重载。
9. 作为一名 7K 玩家，如果我的水平介于离散段位之间（例如 6.2★），我希望支持传入 `--target-sr 6.2` 进行连续星级降阶，以便获得最平滑的个性化进阶梯度。
10. 作为一名音游制谱者，我希望系统提供格式完全合规的 `.osu` 文件导出能力，确保导出的文件能被 osu!stable、osu!lazer 及各类 mania 模拟器 100% 正确解析与无警告加载。
11. 作为一名音游制谱者，我希望导出的练习谱面在元数据中完整保留原始曲目的 Title、Artist、Creator 与音频文件关联，同时在 Tags 中记录原始母谱的 MD5 前缀，以便随时追溯衍生谱面的来源。
12. 作为一名本地工具链用户，我希望通过命令行调用 `python3 -m proj7k.downscaler --input <path> --target-dan 7th`，即可在数秒内完成单个谱面或整个目录的批量降阶。
13. 作为一名算法分析者，我希望降阶器在终端输出降阶前后的 8 维雷达数值对比、峰值应变变化、双手平衡比与被剔除音符数量统计报告，以便全面评估降阶质量。
14. 作为一名 7K 玩家，当原图难度已经低于目标段位、或者无法在保持主导技法前提下降阶时，我希望工具能够明确给出警告并安全退出，而不是输出被破坏的无效谱面。
15. 作为一名系统管理员，我希望联动 `--sync-lazer` 写入数据库时，系统严格复用 `SafeFlushWindow` 探测客户端文件锁，确保绝不在游戏运行关键帧中发生写锁冲突或数据库损坏。

## Implementation Decisions

### 模块划分与架构责任

1. **谱面反向序列化组件 (Beatmap Serializer)**：
   - 在已有的 `parser` 模块中补充反向格式化能力：`dump_osu_7k(beatmap: Beatmap7K) -> str`。
   - 完整还原 `[General]`、`[Metadata]`、`[Difficulty]`、`[TimingPoints]` 与 `[HitObjects]` 段落，确保 mania 7K 轨位与时间戳格式严格对齐。

2. **双层级联段位解析器 (TwoTierDanMapper)**：
   - 加载权威质心指纹库 `docs/research/distilled_ground_truth.json`。
   - 解析目标段位字符串（如 `7th`, `10th`, `Stellium`），获取其在 8 大专项上的基准特征质心与 P90/Top5% 目标应变 $S_{\text{target}}$；若目标为连续星级（`--target-sr`），依据权威段位阶梯标尺（`Canonical Dan Progression Hierarchy`）插值计算目标应变。

3. **骨架锚点探测器 (MetricSkeletonDetector)**：
   - 解析 TimingPoints 计算音符所在小节与拍位网格。
   - 标记两类绝对不可修剪的骨架音符：
     - **强拍底座**：1/1 小节强拍（Downbeat）上的音符，同时间戳至少保留 1 音；
     - **多押基数底座**：并发多押时刻（Chord $\ge 2$），修剪时保证该时间戳剩余音符数 $\ge 1$。

4. **时序应变闭环分批剪枝引擎 (WindowedPeakBatchPruner)**：
   - 内部维护与 `proj7k.strain` 的闭环交互。
   - 每轮运行 `compute_dual_hand_strain`，提取局部应变超过 $S_{\text{target}}$ 的时间窗区间。
   - 计算各候选音符的局部边际应变贡献，结合双手打击通量偏置乘子（`Bimanual Flux Asymmetry Penalty`）动态重排优先级。
   - 批量移除超标窗口内的非骨架音符（单轮剪枝比例控制在 15%~25%），多轮迭代直至全局应变收敛到容差带内。

5. **双轨技法守恒门禁核验器 (DualGateValidator)**：
   - 调用 `proj7k.radar` 重新评估候选谱面的 8 维技法雷达 $\vec{R}_{\text{nerfed}}$。
   - 核验守恒条件：$\cos(\hat{\vec{R}}_{\text{nerfed}}, \hat{\vec{R}}_{\text{orig}}) \ge 0.80$ 且主导技法第一顺位不变，特征质心落在置信容差带内；若违例，执行回滚微调。

6. **本地工具链 CLI 入口与 Lazer Bridge 联动 (`proj7k.downscaler`)**：
   - 暴露命令行接口：
     `python3 -m proj7k.downscaler --input <path> --target-dan <dan> [--target-sr <sr>] [--output-dir <dir>] [--sync-lazer] [--preserve-ratio <float>]`
   - 若指定 `--sync-lazer`，调用现有 `proj7k.sync` 的 Node Realm Bridge 与安全刷盘窗，自动计算离散星级桶标签（`Binned Skill Tag`）并注入 `7K Practice` 游戏内收藏夹。

## Testing Decisions

### 测试接缝设计（Test Seams）

遵循单一高层测试接缝原则，确立 **顶层降阶核心 API 与 CLI 驱动接缝** 为主测点：
- **Python 核心接口接缝**：
  `downscale_beatmap(beatmap: Beatmap7K, options: DownscaleOptions) -> DownscaleResult`
- **CLI 端到端测试接缝**：
  `python3 -m proj7k.downscaler` 进程调用与生成文件验收。

### 优良测试判定准则（What Makes a Good Test）

1. **测试外部可观察行为与物理不变量，不耦合内部循环细节**：
   - 验证输出谱面的 Intrinsic SR 和 P90 应变严格单调下降并收敛至目标段位区间；
   - 验证主导技法（如 Chordjack）在降阶后依然为主导技法，雷达余弦相似度 $\ge 0.80$；
   - 验证所有小节强拍处音符数 $\ge 1$，且全图无突兀空节拍；
   - 验证左右手打击通量比与应变比维持在 $[45\%, 55\%]$ 生理合理区间内。
2. **往返重读合规性（Round-trip Parse Invariant）**：
   - 导出的 `.osu` 文本必须能够被现有的 `parse_osu_7k` 再次无损解析，且解析出的对象与降阶内存对象完全一致。
3. **前置测试基准与先验代码借鉴（Prior Art）**：
   - 参考 `tests/test_strain.py` 中基于合成铺面验证双手机械应变与衰减的测试模式；
   - 参考 `tests/test_radar.py` 中验证 8 维雷达正交抑制与主导技法分类的断言逻辑；
   - 参考 `tests/test_sync_cli.py` 中针对 CLI 参数解析与跨进程桥接的测试规范。

## Out of Scope

1. **音频变速与重采样**：本项目只针对谱面打击物件进行客观拓扑与键力降阶，不改变音频文件的原始播放速率（Rate）。
2. **谱面变速效果与 SV 调制改写**：原谱中的 TimingPoints 速度变倍、视差 Gimmick 保持原样继承，不在本模块进行 SV 简化。
3. **音效（Hitsound / Keysound）重映射**：剔除音符时仅同步移除其携带的打击音效，不进行基于乐理的按键音自动重补。
4. **非 7K 模式支持**：严格遵循 `proj7k` 单一限界上下文，仅支持 Mode 3 (CircleSize 7)。

## Further Notes

- 本规格书完全对齐 [ADR-0011](file:///Users/kz/proj7k/docs/adr/0011-closed-loop-strain-downscaler-and-technique-preservation.md) 与 [`CONTEXT.md`](file:///Users/kz/proj7k/CONTEXT.md)。
- 降阶器生成的衍生谱面 MD5 发生改变，但在 Tags 中携带 `orig_md5_{Hash[:8]}`，使得 `proj7k.live` 实时仪表盘在识别到练习谱面时，能够无缝关联母谱并提供对比视角。
