# 《om7k 铺面固有难度评价与技法解构方法论规范》
# (om7k Intrinsic Difficulty Evaluation & Pattern Deconstruction Methodology Specification)

> **版本**：v1.0.0  
> **状态**：正式规范 (Canonical Specification)  
> **关联架构决策**：[ADR-0001](file:///Users/kz/proj7k/docs/adr/0001-decouple-intrinsic-difficulty-from-player-data.md), [ADR-0002](file:///Users/kz/proj7k/docs/adr/0002-jinjin-dan-as-canonical-technique-benchmark.md), [ADR-0004](file:///Users/kz/proj7k/docs/adr/0004-adopt-7k-vsdl-for-visual-pattern-representation.md), [ADR-0005](file:///Users/kz/proj7k/docs/adr/0005-establish-player-intuition-framework-and-case-sop.md), [ADR-0006](file:///Users/kz/proj7k/docs/adr/0006-om7k-intrinsic-difficulty-master-specification-and-blind-test-validation.md)  
> **领域模型**：[CONTEXT.md](file:///Users/kz/proj7k/CONTEXT.md)

---

## 一、第一性原理与公理基准 (Axiomatic Foundations)

### 1.1 固有难度核心公理 (Axiom of Intrinsic Difficulty)
铺面的**固有难度（Intrinsic Difficulty）**是铺面本身在时空几何排布、神经生物力学负荷、视觉认知阻力与容错脆弱性上的**客观物理属性**。
- **解耦律**：固有难度彻底独立于任何具体玩家的表现数据（如 Performance Points (pp)、Score、Accuracy、Pass-rate 或选手的个人偏科表现）。
- **可复现律**：任何两名掌握相同技法基准的资深玩家，在审视同一段铺面几何切片时，其对于难点位置、卡手成因、容错瓶颈的定性分析具有高度一致的客观收敛性。

### 1.2 权威尺度公理 (Axiom of Canonical Benchmark)
**Jinjin 7K Dan**（包括常规米键 **Regular Dan** 与长押 **LN Dan** 两大独立序列及各自 4 类单一技法练习谱面集）被确立为系统内衡量单一技法难度梯度的**唯一权威基准坐标系**。
- 严禁引入非官方衍生的民间段位或未经严密校验的社区非标铺面作为基准原点；
- 评价任何铺面的难度向量时，必须投影至 Jinjin Dan 的 15 级进阶梯度（0th 起步锚点至 Stellium，见 `CONTEXT.md` 与 `docs/adr/0002`）及 8 类单一技法正交基底上。

---

## 二、7K 生理坐标系与解剖学生理约束 (7K Physiological Coordinate System)

### 2.1 标准键位布局与手指映射
在标准 7K 键盘交互（`SDF` + `Space` + `JKL`）下，操作手指严格遵循由外至内的对称拓扑坐标系：

$$\text{Tracks} = [L_3, L_2, L_1 \mid S \mid R_1, R_2, R_3]$$

| 轨道代号 | 物理序号 | 对应操作手指 | 英文代号 | 解剖生理学特征与腱鞘约束 |
| :---: | :---: | :---: | :---: | :--- |
| **L3** | Col 0 | 左手无名指 | Left Ring | 缺少完全独立的伸肌，依赖指总伸肌；与中指深度腱鞘耦合 |
| **L2** | Col 1 | 左手中指 | Left Middle | 屈肌力量最强，指骨力矩最大，与无名指共享腱间联合 |
| **L1** | Col 2 | 左手食指 | Left Index | 拥有完全独立的示指伸肌（Extensor Indicis），独立性与反应最佳 |
| **S** | Col 3 | 大拇指 | Thumb (Space) | 独立掌骨，对掌肌群运动，敲击行程与其余手指正交 |
| **R1** | Col 4 | 右手食指 | Right Index | 拥有独立的示指伸肌，右半区核心锚点 |
| **R2** | Col 5 | 右手中指 | Right Middle | 屈肌力量最强，与右手无名指共享腱间联合 |
| **R3** | Col 6 | 右手无名指 | Right Ring | 缺少完全独立的伸肌，与中指深度腱鞘耦合 |

> [!IMPORTANT]
> **标准 7K 绝不使用小指（尾指）**。7K 生理机制中最深刻的内在矛盾是**无名指（3 轨）与中指（2 轨）之间的解剖学腱间联合（Juncturae Tendinum）**。

### 2.2 解剖学间距与剪切张力
- 当中指悬空（不按），而同手的无名指与食指同时下砸时（`[gap:1]`，如 $L_3 + L_1$ 或 $R_1 + R_3$），由于横跨了处于中位的中指肌腱，产生强烈的**横向腱鞘剪切应力（Shear Strain）**与被动屈曲冲动，是 7K 中生理张力最高、最易疲劳脱力的手型。
- 相邻并指（`[adj]`，如 $L_3 + L_2$ 或 $L_2 + L_1$）肌腱共动顺畅，但微观高频交替时受屈肌腱共动牵引明显。

---

## 三、7k-VSDL 视觉语言与客观张量标准 (7k-VSDL Specification)

### 3.1 核心六态图元闭集 (Primitive Closed Set)
在自底向上（Bottom-to-Top，时间随文本行递增向上延伸）的矩阵中，各轨在离散时钟 tick 上的状态严格属于 6 态完备闭集：

| 符号 | 图元全称 | 物理定义 | 生理与判定含义 |
| :---: | :--- | :--- | :--- |
| `.` | **Empty (空轨)** | 当前瞬时该轨道无物理输入事件 | 手指处于自由放松悬空态 |
| `o` | **Rice Tap (米键点打)** | 瞬时单点音符按下瞬间 | 瞬时产生判定并立即释放，不独占手指自由度 |
| `⎵` | **LN Head (长条头)** | 长音符按下瞬间 | 产生头部判定，开始独占该轨道手指自由度 |
| `\|` | **LN Hold (长条身)** | 长音符持续按住维持态 | 手指被持续锁定压死（Lockout），压制同手未锁手指上限 |
| `⎴` | **LN Tail (长条尾/释放)** | 长音符抬手释放瞬间 | 产生尾部判定，严格释放时钟，恢复该指物理自由度 |
| `x` | **Overlap (尾按重叠)** | 同轨释放瞬间紧接同轨按键 | $t_{\text{tail}} = t_{\text{head}}$ 的极端高压复用 |

### 3.2 三区手型拓扑签名 (Chord Topology Signature)
针对瞬间多押，使用三区拓扑语法：
$$\text{Signature} = (n_L, n_S, n_R) \quad L\{\text{fingers}\}[\text{topology}] \mid S \mid R\{\text{fingers}\}[\text{topology}]$$

- `[adj]`：相邻无缝并指（$\text{gap} = 0$，如 $\{1,2\}$ 或 $\{2,3\}$）；
- `[gap:1]`：抠空中指间距（$\text{gap} = 1$，如 $\{3,1\}$，中指中空）；
- `[full]`：手部 3 键全砸（$\{3,2,1\}$）；
- `[outer]` / `[mid]` / `[inner]`：单键时分别代表外侧（3）、中间（2）、内侧（1）。

### 3.3 客观 4 轴几何张量 (The 4 Orthogonal Axes)
每个原子连贯切片（0.5 拍 ~ 2 拍）在 4 维正交几何空间中唯一定位：
1. **空间轨迹轴 (Spatial Trajectory)**：`Flow-Mono`（单向流）、`Oscillation`（振荡）、`Convergent/Divergent`（向心/离心）、`Dispersed`（离散）；
2. **并发密度轴 (Chord & Density Waveform)**：`Uniform`（匀质单音流）、`Periodic-Chords`（周期多押）、`Repetition`（静态同形连打）、`Spike`（瞬时爆发峰）；
3. **两手协同轴 (Bimanual Coordination)**：`Unilateral`（单手侧重）、`Interlocking`（交替接力）、`Split-Independent`（双轨解耦）、`Space-Attraction`（中心轨吸附）；
4. **自由度约束释放轴 (Hold Constraint & Release)**：`Free`（零约束纯米）、`Static-Lock(k)`（持续 $k$ 轨锁死）、`Release-Articulate`（动态抬手关节）、`Antiphase`（反相位对偶：跨轨按下瞬间恰为另一轨释放瞬间）。

### 3.4 宏观组合代数 (Compositional Grammar)
- **时序过渡算子 (`->`)**：$M_1 \xrightarrow{[\text{transition}]} M_2$（标注 `smooth`, `spike`, `snap-shift`, `bilateral-mirror` 等演化特征）；
- **空间并联算子 (`||`)**：$M_{\text{Left}} \parallel M_{\text{Right}}$ 或 $\text{Layer}_{\text{Background}} \parallel \text{Layer}_{\text{Foreground}}$（表达双手解耦与复调层叠）。

---

## 四、人类玩家四维直觉阻力模型 (The 4D Intuitive Resistance Model)

```
                     ┌──────────────────────────────────────┐
                     │    人类玩家 7K 铺面直觉阻力模型      │
                     └──────────────────┬───────────────────┘
                                        │
        ┌───────────────────────────────┼───────────────────────────────┐
        ▼                               ▼                               ▼
 【视觉知觉阻力】                【生物力学生理负荷】             【容错雪崩与复合畸变】
- 空间组块失效 (Chunking)       - 腱间联合剪切 ([gap:1])        - 尾判早放级联崩溃 (Cascade)
- 负空间知觉倒错 (Inverse)      - 自由度锁定运动压制 (Hold)      - 邻轨横向漂移失准 (Drift)
- 密度突变视认时延 (Spike)      - 前臂屈肌耐力衰减 (Marathon)    - 非线性相乘畸变 (Δcoupling)
```

### 4.1 维度一：视觉知觉与读谱视认阻力 (Visual Perception)
- **空间组块与分治锚定**：玩家眼球强制将 7 轨划分为 `Chunk_Left[3] + Anchor_Space[1] + Chunk_Right[3]`。打破对称中心锚点或发生外围大跨度晃动将大幅消耗视线扫视时延；
- **负空间知觉倒错**：在高密 LN（LN 占比 $>70\%$）中，屏幕常态被持键压满，认知系统发生“图-底反转”，从“找键按”变为“找缝隙松手”，引发强烈的神经抑制滞后；
- **密度突变时延**：节拍网格瞬时骤变击碎视觉运动外推预测，产生 150ms ~ 250ms 的生理识别迟滞。

### 4.2 维度二：生物力学与腱鞘负荷 (Biomechanics & Neuromuscular Load)
- **指间独立性与剪切**：无名指与中指在 `[gap:1]` 抠空下的剪切张力对神经控制力构成极端挑战；
- **自由度锁定（Degree-of-Freedom Lockout）**：当 $k$ 根手指按住长条时，未锁手指的敲击运动频率上限非线性衰减：
  $$\text{MaxFrequency}_{\text{free}} = f_0 \cdot \left(1 - \alpha \cdot \frac{k}{N_{\text{hand}}}\right)^\gamma$$
- **前臂屈肌乳酸衰减**：持续高密冲击迫使发力杠杆由手指掌指关节转向手腕前臂，在 90s~120s 内触发肌肉耐力衰减。

### 4.3 维度三：容错瓶颈与级联容灾失控 (Tolerance & Cascade Failure)
- **尾判早放级联崩溃**：长条提前释放立即丢失判定，引发玩家本能惊慌回按，导致后续判定时间轴连续错位，形成 5~10 键雪崩断连；
- **空间邻轨漂移**：高频流中微小掌部平移导致手指误入相邻轨道（如 L2 误入 L1），产生双 Miss 且卡死后续击打。

### 4.4 维度四：非线性复合畸变 (Nonlinear Hybrid Coupling)
$$\text{Difficulty}_{\text{Hybrid}}(A \otimes B) = \text{Diff}(A) + \text{Diff}(B) + \Delta_{\text{coupling}}(A, B)$$
单项技法的叠加绝非标量求和，其耦合畸变（$\Delta_{\text{coupling}}$）源自神经中枢冲突（如反相位下砸与抬手同时发生）或运动上限截断（高速米键流遭遇长条锁指）。

### 4.5 反键评判标准校准：BPM 时钟窗口缩放律 (The Inverse BPM Scaling Law)
> [!IMPORTANT]
> **反键（LN Inverse）难度绝非单由持键率（Hold%）或锁指数决定，而是对基准 BPM 及其对应的微观动作窗口 $\Delta t$ 具有极强的条件敏感性。**

- **认知门槛与物理负荷解耦**：
  高密反键（如全屏长条、平均锁指 $>5$）天然具有极强的“认知视觉冲击力”，极易使初学者或静态分析算法产生“此图必然超高难”的虚假高星错觉。但对熟练玩家而言，“图-底反转”只是一次性的读谱习惯适应；**铺面真正的物理固有难度，严格取决于执行抬手与再按压的微观动作时钟窗口 $\Delta t$**。
- **BPM 条件分阶定级标准**：
  1. **低速反键区间 ($\text{BPM} \le 145$，如 140~142 BPM)**：
     - 动作时钟窗口充裕（8 分音符间隔 $\Delta t \ge 210\text{ms}$），手指屈伸肌群拥有充足的动作重置时间；
     - **资深玩家真实视角校准**：纯低速反键铺面，即使持键率接近 100%、锁指 5 根以上，**其实际综合固有难度上限最多只有 5 星（4★~5★）左右**；严禁脱离 BPM 将其过度评估为 9★~10★；
  2. **中速反键区间 ($\text{BPM} \in [150, 180]$)**：
     - $\Delta t$ 缩短至 $100\sim 150\text{ms}$，抬手判定与反相位神经冲撞开始对肌腱控制构成实质性抗阻，难度进阶至 6★ ~ 8★；
  3. **高速反键极限区间 ($\text{BPM} \ge 190 \sim 240+$，如 Jinjin 10th/Gamma/Zenith/Stellium)**：
     - $\Delta t \le 60\sim 80\text{ms}$，在持续锁死 4~5 指的极端拘束下，剩余手指必须在 60ms 极窄时钟内完成高频反相位下砸与精准抬手，生理自由度压制与运动上限相乘爆发，**在更高 BPM 下维持同样密度才会真正跃升为 9★ ~ 10★+ 的人类技术极限**。

---

## 五、标准化六步审问与逆向归纳 SOP (The 6-Step SOP)

1. **Step 1：全景扫描与物理时钟锚定**
   - 提取段落基准 BPM、16 分音符时钟间隔 $\Delta t_{1/16}$、LN 数量占比、宏观 NPS；
   - 调用切片工具渲染标准化垂直卷轴切片长图。
2. **Step 2：7k-VSDL 微观状态与多押手型标注**
   - 生成自底向上时间矩阵，核准 6 态图元；
   - 提取并标注所有瞬间多押拓扑签名（`[adj]`, `[gap:1]`, `[full]` 等）。
3. **Step 3：原子连贯窗口划分与 4 轴客观几何定型**
   - 基于流向折返、拍号量化突变、双手解耦、长条约束启闭等 4 类边界事件划分 0.5~2 拍原子切片；
   - 评定空间轨迹、并发密度、两手协同、自由度约束 4 轴几何坐标。
4. **Step 4：宏观多声部图谱与物理负荷计算**
   - 运用 `->` 与 `||` 算子构建宏观时空代数方程；
   - 测算平均锁指自由度（Mean Locked Fingers）、并发峰值比与单双侧击打负荷比。
5. **Step 5：四维直觉阻力交叉审问 (Checklist 审查)**
   - 逐项审查：视认组块破坏度、负空间倒错率、`[gap:1]` 生理剪切频次、自由度压制深度、尾判早放级联脆弱点、复合耦合畸变项。
6. **Step 6：Jinjin Dan 基准对齐与模式库归纳**
   - 与 Jinjin Regular Dan / LN Dan 15 级段位序列及 8 项单一技法练习集进行难度与风格投影，产出结构化归纳卡片（Induction Card）。

---

## 六、视角复刻盲测验证协议与一致性评分指标 (Blind Testing Protocol & Rubric)

### 6.1 盲测原则与信息遮蔽协议 (Information Masking Protocol)
为验证 AI 模拟玩家视角分析时是否真实复刻了人类资深玩家的审题本能与归因切入点，必须执行严格的**信息遮蔽盲测**：
1. **遮蔽项**：严禁向分析模型输入社区对该图的难度争论结论、非官方评价标签、游玩通关数据或既有题解；
2. **暴露项**：仅向分析流程输入客观 `.osu` 物理数据、时间戳切片图与 7k-VSDL 矩阵；
3. **输出要求**：独立输出 4 维直觉阻力定性分析、核心暴毙点机理推导、以及 Jinjin Dan 段位归属预测。

### 6.2 一致性评分准则 (Consistency Rubric)
盲测结果与人类资深玩家共识及 Jinjin Dan 官方标准在以下 3 个核心维度上进行比对打分（满分 100 分）：

| 评估维度 | 权重 | 判定标准与达标线 |
| :--- | :---: | :--- |
| **1. 审题视角对齐度 (Perspective Alignment)** | 35% | 能否第一眼抓住玩家最痛苦的核心矛盾（如负空间倒错、`[gap:1]` 剪切、大拇指枢纽失调等），而非泛泛罗列 NPS 或音符总数。 |
| **2. 难点归因一致率 (Causality & Bottleneck Consensus)** | 35% | 定性的“核心暴毙点”、“断连连带效应”与人类资深玩家实战暴毙点是否严格吻合（精确到具体轨道、毫秒区间与动作冲突）。 |
| **3. 段位锚定命中率 (Dan Tier Benchmark Accuracy)** | 30% | 预测的段位归属（Regular / LN Dan）与官方/权威难度梯度的绝对偏差 $\le \pm 1$ 个段位。 |

- **综合合格线**：综合总分 $\ge 85$ 分且单项不低于 80 分，判定为该测试用例通过视角复刻一致性验证。

---

## 七、规范实施与演进路线 (Evolution Roadmap)

1. **阶段一（当前已就绪）**：确立方法论与描述语言，完成标杆切片工具与分析器开发，通过代表性盲测样本集验证视角复刻一致性；
2. **阶段二（后续推进）**：以 Jinjin Dan 15 级段位曲目库（含 0th 起步锚点）为基础，批量执行自动化经验蒸馏，沉淀全量 7K 技法模式库；
3. **阶段三（引擎落地）**：基于沉淀模式库与 4 维阻力模型，实现全自动 om7k 固有难度评估与技法多维雷达引擎。
