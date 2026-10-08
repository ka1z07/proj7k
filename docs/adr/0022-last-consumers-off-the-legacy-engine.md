# 0022. 最后一批消费者离开旧引擎

- **状态**：accepted（2026-10-08；承接 ADR-0020/0021，为删除旧引擎做准备）。
- **背景**：ADR-0020/0021 把 profiler 与下缩链的计算迁到了难度场上，但旧模块仍被引用：profiler 与下缩器从 `radar` 取技法短键表 `TECHNIQUE_NAMES`，`aggregate` 从 `rating` 取玩家侧 p 范数，`pathology` 从 `radar` 取和弦分步，`mapper` 为旧 `strain` 保管应变律的反函数，`validator` 留着取旧 `TechniqueRadar` 的余弦；live 帧的 `legacy` 键（左右手应变画布、4D Tech 拆解、旧合成星）每帧还在跑整套旧引擎。只要这些引用在，旧模块就删不掉。

## 决策内容

1. **技法短键表归难度场。** `field.TECH_KEYS` 按引擎技法顺序给出 `jack`、`tech`、…、`ln_release`（即 `SKILL_TECH_KEY` 的值）。放在 `field` 而不放进 `engine` 包，原因同 ADR-0020：引擎版本令牌摘要包内每个模块，消费者侧的改动不应让已盖章的谱面过期。
2. **玩家侧总评归 profiler。** `player_overall_star` 自带 p 范数（p = 4、阻尼 0.08，数值不变），不再借 `rating.aggregate_p_norm`。
3. **和弦分步归 `pathology`。** 它是回放病理检测的一部分，复制为 `pathology._partition_chord_steps`（容差 8 ms，同旧值）。
4. **旧东西随旧模块走。** 应变律反函数 `star_rating_to_strain` 搬回 `strain`（它唯一的使用者）；`validator.compute_radar_cosine_similarity` 删除，其测试改测 `compute_skill_cosine_similarity`。
5. **live 时间线 = 难度场。** 帧里的 `legacy` 键删除，换成 `timeline`：`field.curve(bin_s=1.0, start_s=0.0)` 的 `load`/`risk`/`skill`/`events`，外加 `skills`（`TECH_KEYS`，`skill` 下标对应的短键）。面板的时间线画法与复盘查看器一致（ADR-0021 决策 3）：柱高是 `d_i / D` 的段内最大值，柱色是承载风险的技法，虚线是 1.0（谱面自身水平），白线是期望丢失；播放头照旧。一帧只跑一次引擎：`trace_osu` 的 `profile` 就是 `evaluate_osu` 的结果。
6. **4D Tech 拆解删除。** 流向紊乱度、括号剪切、轨位熵、节奏混乱度、微观爆发是旧驱动层的中间量，新引擎没有对应物；面板底部的抽屉随之删除。

## 后果

- `src/proj7k` 里旧模块以外的代码不再 import `radar`、`strain`、`rating`、`difficulty`、`calibration`。剩下的引用者是批量工具的旧指标（`benchmark_core` 与 `tools/` 下的标定脚本）和旧机制自己的测试，下一步与旧模块一起删除。
- OBS 叠层的 `?strain=1` 参数仍控制时间线卡片的显隐，含义不变。
- issue #46 中「核对 live 页面在 OBS 叠层模式下读 `legacy` 键后的表现」一项随 `legacy` 键删除而不再适用。
