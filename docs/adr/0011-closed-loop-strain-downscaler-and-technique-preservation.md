# 0011. 闭环时序应变降阶与技法守恒架构 (Closed-Loop Strain Downscaler & Technique Preservation)

- **状态**：accepted
- **日期**：2026-09-17
- **背景**：
  玩家在面对超越当前生理与认知上限的高难 7K 铺面（如 Stellium 段位、极限 Chordjack 或密排反键 LN）时，常因局部致死峰值（Burst Peak）或长程腱力枯竭而无法有效练习。传统的播放变速（如 0.8x / 0.9x Rate）会强行改变音乐原速与判定时钟窗口，且无法针对性化解特定图元对生理自由度的过度压制；而粗暴的随机抽稀则会破坏节奏骨架并导致技法属性失真（例如将弦叠劣化为散乱切键）。
  因此，系统需要在本地工具链中构建一套以客观固有难度为导向的**铺面降阶器 (Beatmap Downscaler)**，生成具备真实训练价值的**衍生练习谱面 (Derivative Practice Beatmap)**。

## 决策内容

1. **目标控制与双层级联段位映射 (Two-Tier Dan Cascade Mapping)**：
   - 以 Jinjin 7K Dan 作为核心降阶目标参数（`--target-dan`），同时开放连续星级（`--target-sr`）与专项参数覆写。
   - 解算目标应变时采用双层级联：第一层优先检索 120 首标杆曲目蒸馏出的权威质心指纹库（`distilled_ground_truth.json`），提取目标段位在该主导专项上的基准应变 $S_{\text{target}}$、`peak_4m_nps` 与雷达轮廓；第二层回退至 15 级权威段位与固有星级标尺进行平滑插值。

2. **纯音符剔除变异算子 (Pure Deletion Mutation)**：
   - 变异动作空间限定为原子级音符剔除：米键（Rice）直接移除时间戳与轨位记录；长音符（LN）头、身、尾一体完整移除。
   - 杜绝在降阶过程中产生中间态或人工截断的畸形微观图元，确保衍生谱面在图元闭集与编辑规范上与官方标准 100% 同构。

3. **闭环峰值时间窗分批剪枝策略 (Windowed Peak-Batch Pruning)**：
   - 将 `proj7k.strain` 连续应变衰减累积模型作为自适应优化的适应度函数。
   - 依据双通道时序应变曲线 $S(t)$ 精准定位所有超过目标阈值 $S_{\text{target}}$ 的局部时间窗（Peak Windows）。
   - 在各超标窗口内，对非骨架音符按边际应变贡献降序排列，按批次（单轮 15%~25%）修剪高应变候选，经多轮迭代前向重评，实现数秒内的稳定数学收敛。

4. **复合骨架与双手通量动态平衡约束 (Invariants & Biomechanical Constraints)**：
   - **复合骨架约束 (Composite Metric Constraint)**：基于 TimingPoints 网格，1/1 小节强拍（Measure Downbeat）至少保留 1 音底座；任意时刻的并发多押（Chord $\ge 2$）只削减押数，绝对禁止整拍事件全删为空轨，杜绝突兀静音。
   - **双手通量失衡惩罚 (Bimanual Flux Asymmetry Penalty)**：计算候选音符修剪优先级时引入两手通量比动态乘子，若某侧手打击通量已偏低，则抑制其继续被修剪，自适应平抑原谱潜在的单侧偏载，防范练习时代偿性肌肉劳损。

5. **双轨技法守恒门禁 (Dual-Gate Technique Preservation)**：
   - **宏观轮廓守恒**：降阶前后 8 维技法雷达向量方向余弦相似度 $\cos(\hat{\vec{R}}_{\text{nerfed}}, \hat{\vec{R}}_{\text{orig}}) \ge 0.80$，且原谱第 1 顺位主导技法在降阶后严格保持不变。
   - **微观质心置信区间**：主导技法的核心生理指标（如 Chordjack 的 $\Delta k=1$ 停滞密度、LN 的持握空间通量）必须严格落在目标段位质心的置信容差带内，严防图种本质退化。

6. **全息追溯元数据与本地客户端联动 (Full-Provenance & Lazer Bridge Pipeline)**：
   - 衍生谱面保存在原谱面集同级目录下，难度名统一遵循 `[P-{TargetDan} {DominantSkill}] {OriginalVersion}` 规范。
   - 在 `.osu` 元数据 Tags 中写入 `proj7k_downscaled`、`target_{TargetDan}`、`dominant_{DominantSkill}` 与原始母谱哈希 `orig_md5_{Hash[:8]}`。
   - 提供 `--sync-lazer` 开关，生成后自动通过 Node Realm Bridge 刷新 osu!lazer 谱面索引，并安全刷盘注入 `7K Practice` 专属收藏夹。

## 权衡与取舍 (Trade-offs)

- **为什么采用闭环应变反馈而非纯静态启发式规则**：
  纯启发式规则（如“逢双押删一轨”或“固定按百分比降频”）完全脱离生理键力时序模型，无法预知删除后实际难度是否落在目标段位，极易出现局部欠修剪（依然致死）或全局过度修剪（难度暴跌）。以时序应变作为闭环适应度函数，能精准靶向致死波峰并保证难度严格收敛。
- **为什么变异动作选择纯音符剔除而非 LN 降解为米键 (LN-to-Rice)**：
  LN-to-Rice 虽然能保留击点律动，但在复杂混押（如 LN Tech / Inverse）中，将长条降解为米键会瞬间改变轨位占用关系，使得后续原本规避同轨的散点意外变成连续叠键（$\Delta k=1$），反而人为引入高难 Jack。纯音符剔除在拓扑上严格单调递减所有维度的物理键力，数学性质最为干净可控。
- **为什么采用双手失衡动态惩罚而非强制 1:1 交替修剪**：
  高难谱面中常存在明显的单手 solo 或非对称偏载段落。若机械执行 1:1 轮流修剪，原本负荷较低的手会被削弱到几乎空闲，而承载重压的手依然超标。引入失衡惩罚乘子能够自适应对重压手进行“削峰填谷”，形成最适宜进阶练习的生理负荷曲线。
