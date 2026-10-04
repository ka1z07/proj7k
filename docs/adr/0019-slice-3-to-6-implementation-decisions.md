# 0019. 切片 ③–⑥ 的实施裁定 (Implementation Decisions of Slices 3–6)

- **状态**：accepted（2026-10-04；落实 ADR-0017/0018，分支 `engine/spec-v0.2`）。
- **背景**：ADR-0018 把几处细节留给实施时定。以下是实施中实际做的选择与理由，范围只到新引擎的 A、B 组消费者；C、D 组（profiler、pruner 的应变驱动、旧应变单位）仍按 ADR-0018 决策 4 留在旧引擎。

## 决策内容

1. **自有技能阶梯 (`own_skill`)**：每个标杆池只对它自己的技能那一条阶梯设闸门（`engine.skills.BENCHMARK_POOL_SKILL`：Regular Jack→`rc_jack` … LN Release→`ln_release`）。实现为单一指标名 `own_skill`（`monotonicity.OWN_SKILL_METRIC`），按结果所在池取技能，所以没有任何池会被判在它不属于的技能上。默认闸门与总星级同款（τ ≥ .88、ρ ≥ .95、违规 ≤ 4），`guard.OWN_SKILL_RATCHET` 按池覆盖，上限取实测值：八池里只有 LN Release 低于通用线（τ .829 / ρ .939 / 4 处倒挂），其余七池的条目只防止放松。旧引擎的 `driver_*` 指标与闸门原样保留，给仍读旧驱动的测试与工具。
2. **段位表与读法**：`dan_table.json` 是十五档 RC 段中位星数，两位小数；`estimate_canonical_dan` 取按比值最近的一档（相邻两档的几何平均为分界），低于 0th 与高于 Stellium 各归两端。旧表与旧阈值改名 `LEGACY_DAN_SR` / `legacy_estimate_canonical_dan`，profiler、coach、下缩映射器与 `rating` 仍用它们。四档锚点区间按八池中位数重基线化（0th 2.5–3.8、5th 5.0–6.2、10th 7.2–8.5、Stellium 10.5–13.0）；新标尺没有软上限，原 12.5 的上限改为 13.0 的防爆炸界。
3. **技法标签沿用旧短键**：守护进程与下缩写入游戏库的主导技法仍是 `jack`、`tech`、`speed`、`stream`、`ln_*`（`engine.skills.SKILL_TECH_KEY`），不改成 `rc_stamina` 等引擎名。游戏内按标签筛选的习惯、双轴收藏夹的名字因此不变；代价是标签 `stream` 在新引擎里指 `rc_stamina`（和弦串），语义随 ADR-0017 变了而名字没变。ADR-0018 后果一里留的"是否保留别名"按保留裁定；`--dry-run` 报告的就是旧标签到新标签的迁移量。
4. **注入版本令牌**：`v` + 新引擎的方法学摘要（引擎包全部模块的 AST、常数、锚点文件，外加 `dan_table.json`，因为段位名是注入文本的一部分）。旧引擎注入的谱面因此全部读作过期，下一轮同步会重新评估；先用 `python3 -m proj7k.sync --once --dry-run` 看影响：将改写多少张、已注入谱面的平均星级变化、旧新主导标签的迁移计数，不快照、不加锁、不写库，osu!lazer 运行中也能跑。
5. **live 一帧**：`star_rating`、`dan_tier`、`radar`（画布仍读同样八个键）来自新引擎，完整画像在 `profile` 下；双手应变画布、4D 技法拆解与旧合成放在带标注的 `legacy` 键里，页面从那里读。`raw_star_rating` 不再存在（新引擎没有"未压缩"之说）。
6. **下缩链**：`DualGateValidator` 对八技能星数向量取余弦（≥ 0.80）并要求新引擎的主导技能不变；流水线与映射器的主导技能同样取自新引擎，再换成短键喂给修剪器。闭环仍以旧星级与应变为目标，CLI 报告里的星级因此标 "legacy SR"。原图的画像在一次下缩里只解一次（验证器按对象记忆）。
7. **`benchmark_core` 与两件标定工具**：只服务旧引擎的星标尺，标为旧引擎、不迁移；新标尺的拟合由 `tests/engine/test_engine_scale.py` 持有。
8. **退化谱面**：总难度为 0（没有可解的东西，如两三个音符）时，引擎读数为零、数值有限、无 numpy 警告：θ=0 时的损失取极限，主导度在无事件份额时退化为等权。

## 后果

- 闸门三个入口的当前基线：测试 677 通过、2 失败（旧引擎的两个预先存在项），`--guard` 通过。
- 两个遗留的开放问题仍在工单草稿里：C/D 组"单一应变场里什么是应变"，以及新引擎 T1 的 12 处倒挂记账。
