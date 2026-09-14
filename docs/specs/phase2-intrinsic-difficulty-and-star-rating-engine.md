# Phase 2 铺面固有综合难度评级 (Star Rating) 与技法雷达计算引擎工程规格书

## Problem Statement

当前 om7k 难度重构系统（`proj7k`）已在 Phase 1 成功完成了 120 首 Jinjin 7K Dan 权威标杆曲目的全量批处理、13 维生理与微观时空特征提取、梯级单调性评估与正交特征指纹蒸馏（Issue #8），并在本地曲库中取得了全局秩相关系数 Spearman $\rho > 0.98$ 的实证校验。

然而，项目依然面临从“底层特征统计”跨越到“玩家可感知、可检索、可对齐的权威难度评级系统”的核心瓶颈：

1. **官方 Star Rating (SR) 评级机制彻底失衡**：现存 osu!mania 官方 SR 算法严重偏向单一纯密度（NPS），完全无视 7K 空间双手解耦、生理指距负荷（如 `[gap:1]` 抠空中指）、同轨连打疲劳，且在长条（LN）反键上表现出荒谬的低速虚高与高速失效。这导致玩家社群完全无法依据官方星级进行训练选曲与水平衡量。
2. **缺乏多维能力雷达与综合单一星级的统一映射理论**：根据 ADR-0002，7K 难度由 8 大正交技法（Jack、Tech、Speed、Stream、LN General、LN Tech、LN Inverse、LN Release）共同构成。然而，传统的简单算术加权平均会将单项极致偏科高难图（如纯 10 段 Jack 但无长条）无情稀释为低星谱面；而简单的取最大值（$\max$）又无法度量多维混押谱面的复合生理消耗。系统亟需一套兼顾“单项极值占优”与“多维混押协同增益”的非线性范数聚合算子。
3. **物理键力时序与读谱认知负荷缺乏微观动态耦合**：静态的整谱宏观特征无法度量局部致命段落（Diff Spike）与长线耐力耗竭（Stamina Drain）的本质区别；且在高速高密长条下，若忽视判定时间窗相对于动作时钟的容错重叠缓冲，会导致模型产生脱离人类生理现实的天文数字星级膨胀。

若不建立一套纯客观、基于第一性原理且与 Jinjin 7K Dan 权威段位严格单调锚定的综合难度计算引擎，整个 om7k 工具链的后续多维检索、玩家回放诊断与智能推图系统将失去度量衡基石。

---

## Solution

构建 **Phase 2 铺面固有综合难度评级 (Star Rating) 与技法雷达计算引擎 (Two-Layer Intrinsic Difficulty & Radar Engine)**。

该引擎从底层物理肌腱负荷到上层读谱认知阻抗实现双层解耦与非线性调制，其核心由四大核心支柱构成：
1. **双手独立生理应变池与时序衰减器 (Dual-Hand Decoupled Strain Accumulators)**：解耦左三轨（`L3, L2, L1`）与右三轨（`R1, R2, R3`），分别模拟单手肌腱在微观切片（0.5~2 拍）上的短期充血与半衰期（$\tau = 1.2\text{s}$）恢复，捕捉单手负荷失衡（One-hand bias）与耐力枯竭。
2. **微观击键神经爆发与判定窗口重叠缓冲调制**：
   - 物理层注入微观爆发神经应变（Micro-Speed Burst Strain），精确量化单手连续击键 $\Delta t < 110\text{ms}$（等效全谱 $> 272\text{BPM}$）时的动作电位不应期激增；
   - 认知层建立基于判定窗口（$W_{\text{judg}} \approx 38\text{ms}$）占打击窗口比例（$\eta$）的饱和收敛算子，准确刻画顶尖玩家利用判定窗容错“粘键/滑键”缓解高速机械区认知阻抗的真实生理机制。
3. **8 维技法雷达单项分标定与底噪交叉抑制**：
   基于物理专有算子主导定标，并结合 Phase 1 导出的专项分离度矩阵进行正交底噪交叉抑制，输出 8 维标准化能力雷达标量 $\vec{R} = [r_1, \dots, r_8]$。
4. **极值占优广义范数与双曲正切软上限压缩**：
   采用 $p = 4.0$ 极值占优广义范数（Extremum-Dominant $p$-Norm）合成综合 Star Rating，确保单项偏科图保留 $96.5\%$ 极值强度，同时复合图享有 $+1.26★$ 的合理混押增益；在超过 9.5★ 之上引入双曲正切（$\tanh$）软上限压缩，将全服最极限的人类不可逾越神谱平滑收敛在 $12.5★$ 封顶区间。

---

## User Stories

1. As a 7K 竞技玩家, I want 铺面能够显示一个与真实体感严格吻合的客观综合难度星级 (Star Rating), so that 我不再受官方失衡的虚高/虚低星级误导。
2. As a 7K 竞技玩家, I want 查阅铺面的 8 维技法能力雷达 (Jack, Tech, Speed, Stream, LN General, LN Tech, LN Inverse, LN Release), so that 我能精准掌握谱面的具体技能侧重点与弱项考点。
3. As a 7K 竞技玩家, I want 极高难度的单一偏科谱（如纯 10 段 Jack 但完全没有 LN）依然能获得匹配其高难度的 7.5★ 评级, so that 我的键力专项训练谱面不会因为缺少长条被算法平均到 5★。
4. As a 7K 竞技玩家, I want 一张在多项技法上都具备高难度的混押谱面（如 Tech + Inverse 复合）能获得高于单一维度的综合协同星级加成, so that 算法准确体现复合谱面综合读谱与控键的极高门槛。
5. As a 7K 竞技玩家, I want 极端高速长条谱面（如 240BPM 全反键）拥有全游最高的星级地位，但数值被平滑收敛在 12★~12.5★ 以内, so that 排行榜星级比例保持直观与严肃，不出现荒谬的 20+ 星膨胀。
6. As a 7K 段位挑战者, I want 难度引擎给出的星级与 Jinjin 7K Dan 段位序列（0th 至 Stellium 共 15 级）严格单调递增, so that 我可以用星级作为稳健的段位进阶路标。
7. As a 7K 谱师 (Mapper), I want 获得双手独立应变时间序列曲线图, so that 我能识别出谱面中哪一小节对单手造成了过载压迫，避免设计出生理畸形的单手瘫痪配置。
8. As a 7K 谱师, I want 算法能准确识别 300BPM 高速散打（Speed）与 150BPM 密集多押（Chordstream）的物理难度差异, so that 我的高频单音推速图能得到公正定级。
9. As a 7K 谱师, I want 调整谱面中长条的释放尾点时能观察到 LN Release 雷达分值的敏锐响应, so that 我能精确掌控长条尾判的苛刻程度。
10. As a 难度算法工程师, I want 引擎提供单一顶层入口 `evaluate_intrinsic_difficulty`, so that 我可以通过一行代码对任意 `.osu` 谱面完成全量时序仿真与星级推算。
11. As a 难度算法工程师, I want 算法在处理几千小节的长篇马拉松谱面时具备流式分位数池化 (P90) 能力, so that 局部单点爆发与长程耐力消耗能够以极高数学单调性被统一表征。
12. As a 难度算法工程师, I want 双手解耦衰减器支持可配置的肌腱半衰期恢复常数 ($\tau = 1.2\text{s}$), so that 能够准确模拟小节间歇（Rest measure）带来的手部乳酸消退。
13. As a 难度算法工程师, I want 物理键力层与认知阻抗层保持乘法调制耦合 ($D = L_{phys} \cdot (1 + \alpha L_{cog})^\gamma$), so that 在没有按键（0 NPS）时系统绝不产生虚假认知难度。
14. As a 难度算法工程师, I want 认知阻抗算子动态融合判定窗口比例 $\eta = W_{\text{judg}} / \Delta t_{\text{action}}$, so that 高速机械区的微观释放压力不会陷入纯指数无限膨胀。
15. As a 难度算法工程师, I want 引擎输出包含 $L_{phys}$、$L_{cog}$、8 维原始雷达与综合星级的数据契约实体, so that 系统的所有中间判定步骤均可完全透明溯源。
16. As a 7K 曲库分析与平台开发者, I want 难度引擎提供毫秒级计算吞吐（单谱计算耗时 $< 20\text{ms}$）, so that 能够为全量数万张 om7k 谱面实施服务端离线跑批与实时上传分析。
17. As a 7K 曲库分析与平台开发者, I want 能够基于 8 维能力向量构建数据库空间索引, so that 玩家能够在 Web 端发起“Jack 相当于 6 段但 LN 低于 4 段”的高维复合条件检索。
18. As a 7K 赛事主办方 (Tournament Organizer), I want 使用该引擎对比赛选图（Map Pool）进行严格的技法分类与难度校准, so that 彻底规避选图失衡与选手争议。
19. As a 7K 赛事主办方, I want 算法给出谱面的稳定性风险系数（Spike-to-P90 Ratio）, so that 我能区分出适合作为稳定决胜图还是高暴毙率观赏图。
20. As a 训练与反馈系统设计者, I want 难度引擎的技法雷达能够与玩家个人能力雷达在同一度量衡下直接相减, so that 系统可以自动化定位玩家在击打某张图时的生理与认知过载短板。
21. As a 核心引擎 CI/CD 自动化守护进程, I want 具备端到端黄金回归守卫, so that 核心衰减公式或范数参数的任何误改都会因基准星级偏移或倒挂被立即拦截。
22. As a 核心引擎 CI/CD 自动化守护进程, I want 引擎在浮点运算上保持确定性跨平台一致性, so that 在 Linux 构建机与 macOS 开发机上计算出的星级校验和完全一致。
23. As a 7K 资深竞技玩家, I want 即使是不规则变速（BPM Gimmick / SV）谱面，难度引擎也只依据击键实际物理时间戳结算, so that 视觉变速障眼法不干扰铺面固有难度的客观计算。
24. As a 7K 资深竞技玩家, I want 慢速反键（如 79BPM 6段）被合理限制在初中级难度区间, so that 不会因为长条面积铺满屏幕而产生不合理的超高定级。
25. As a 难度算法工程师, I want 引擎代码模块与 Phase 1 双层缓存结构无缝兼容, so that 已解析的 AST 与切片可被难度计算引擎瞬间复用。

---

## Implementation Decisions

### 1. 核心架构与模块规划

难度引擎确立为独立、深度的核心计算模块，位于 `src/proj7k/engine/` 或集中于高内聚的难度核心接口下：

```text
src/proj7k/
├── difficulty.py          # 顶层核心引擎契约与单谱/批量评级对外入口
├── strain.py              # 双手解耦物理/认知应变时序累积器 (Dual-Hand Slicing Strain)
├── radar.py               # 8 大专项雷达单项分标定与正交底噪交叉抑制
└── rating.py              # 极值占优 p-Norm 合成与双曲正切软上限压缩算子
```

### 2. 核心数学模型与算子规格（基于原型实证固化）

根据 Throwaway Prototype 在 120 首标杆曲目上的实证，模型严格固化以下数学契约：

#### (1) 双手解耦微观时序应变积分
将谱面按滑动窗口（$\Delta W = 0.5\text{s}$，步长 $\text{step} = 0.25\text{s}$）切片，左手通道（轨 0, 1, 2 + 50% 轨 3）与右手通道（轨 4, 5, 6 + 50% 轨 3）各自独立维护肌腱疲劳蓄能池：

$$S_{\text{hand}}(t) = S_{\text{hand}}(t-1) \cdot e^{-\Delta t / \tau_{\text{hand}}} + D_{\text{hand}}(t)$$
$$\tau_{\text{hand}} = 1.2\text{s}$$
$$S(t) = \sqrt{S_L(t)^2 + S_R(t)^2}$$

#### (2) 物理键力与认知阻抗调制
单手瞬时难度 $D_{\text{hand}}$ 遵照阻抗调制契约：

$$D_{\text{hand}} = L_{phys, \text{hand}} \cdot \left(1.0 + \alpha \cdot L_{cog, \text{hand}}\right)^\gamma \quad (\alpha = 0.60, \gamma = 1.20)$$

其中物理键力包含击键通量、微观神经爆发、同轨连击与指距剪切：
$$L_{phys, \text{hand}} = \text{NPS}_{\text{hand}} \cdot \left(1.0 + 0.30 \cdot \frac{\text{SpeedBurst}_{\text{hand}}}{\max(1, N_{\text{notes}})}\right) \cdot \left(1.0 + 0.18 \cdot \text{Jack}_{\text{hand}} + 0.20 \cdot \text{Gap1}_{\text{hand}}\right)$$
$$\text{SpeedBurst}_{\text{hand}} = \sum_{5\text{ms} < \Delta t < 110\text{ms}} \left(\frac{110\text{ms} - \Delta t}{50\text{ms}}\right)^{1.35}$$

其中认知阻抗受判定窗口重叠缓冲比率 $\eta$ 调节：
$$\eta = \min\left(0.75, \frac{W_{\text{judg}}}{\Delta t_{\text{action}}}\right) \quad (W_{\text{judg}} = 38\text{ms})$$
$$\text{ScalingFactor} = \begin{cases}
\left(\frac{\text{BPM}}{145}\right)^{1.8} \cdot 0.75, & \text{BPM} \le 145 \\
0.75 + 0.25 \cdot \left(\frac{\text{BPM} - 145}{35}\right), & 145 < \text{BPM} < 180 \\
1.0 + 0.65 \cdot \left(\frac{\text{BPM} - 180}{40}\right) \cdot (1.0 - 0.45 \cdot \eta), & \text{BPM} \ge 180
\end{cases}$$
$$L_{cog, \text{hand}} = 0.35 \cdot \left(\frac{\text{LockedFingers}_{\text{hand}}}{3}\right) \cdot \text{ScalingFactor} + 0.15 \cdot \text{Antiphase}_{\text{hand}}$$

#### (3) P90 分位数时空池化
从全谱时序 $S(t)$ 中提取稳态核心难度：
$$S_{\text{base}} = \text{Percentile}_{90}\left(\{S(t)\}\right)$$

#### (4) 8 维技法雷达向量与正交交叉抑制
每个专项分值 $r_k$ 由其物理主导算子得出，并与专项分离度矩阵 $\mathbf{M}_{\text{sep}}$ 施加底噪抑制截断：
$$\vec{R} = [r_{\text{jack}}, r_{\text{tech}}, r_{\text{speed}}, r_{\text{stream}}, r_{\text{ln\_gen}}, r_{\text{ln\_tech}}, r_{\text{ln\_inv}}, r_{\text{ln\_rel}}]$$

#### (5) 极值占优 $p$-Norm 与双曲正切软上限压缩
综合单一 Star Rating 由 $p = 4.0$ 广义范数合成，并在超过 9.5★ 后平滑渐进饱和：

$$SR_{\text{raw}} = a \cdot S_{\text{base}}^{0.65} + b$$
$$SR_{\text{norm}} = \max(\vec{R}) \cdot \left(\sum_{k=1}^8 \left(\frac{r_k}{\max(\vec{R})}\right)^p\right)^{1/p} \cdot \left(1.0 + 0.08 \cdot \sum_{k=1}^8 \left(\frac{r_k}{\max(\vec{R})}\right)^p\right)^{-0.5}$$
$$SR_{\text{final}} = \begin{cases}
SR_{\text{norm}}, & SR_{\text{norm}} \le 9.5 \\
9.5 + 3.0 \cdot \tanh\left(\frac{SR_{\text{norm}} - 9.5}{3.0}\right), & SR_{\text{norm}} > 9.5
\end{cases}$$

### 3. 数据契约定义 (Data Contract Schema)

```python
@dataclass(frozen=True)
class TechniqueRadar:
    jack: float
    tech: float
    speed: float
    stream: float
    ln_general: float
    ln_tech: float
    ln_inverse: float
    ln_release: float
    dominant_technique: str
    dominant_score: float

    def to_dict(self) -> Dict[str, Any]:
        ...

@dataclass(frozen=True)
class StrainTimeseriesProfile:
    step_seconds: float
    times: List[float]
    left_hand_strain: List[float]
    right_hand_strain: List[float]
    combined_strain: List[float]
    p90_strain: float
    p95_strain: float
    peak_strain: float

@dataclass(frozen=True)
class IntrinsicDifficultyResult:
    star_rating: float
    raw_star_rating: float
    radar: TechniqueRadar
    strain_profile: StrainTimeseriesProfile
    metadata: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        ...
```

---

## Testing Decisions

### 1. 良好测试的定义 (What Makes a Good Test)

本系统所有测试必须严格遵循**高测试接缝外在黑盒契约断言**：
- **禁止断言内部中间变量**：不测试循环内部累加器中间步数值、不测试局部字典的临时引用。
- **专注系统外在行为与数学不变性**：
  1. **单调性守卫**：输入 120 首标杆曲目，断言其在 15 级段位阶梯上的 Spearman 秩相关系数 $\rho \ge 0.98$，Kendall $\tau \ge 0.94$，相邻段位倒挂数低于容差门限；
  2. **锚点数值守恒**：断言 0th Dan 中位数落于 $[3.2, 3.8]$★，5th Dan 中位数落于 $[5.1, 5.8]$★，10th Dan 中位数落于 $[7.2, 8.0]$★，Stellium 落于 $[10.0, 12.5]$★；
  3. **偏科谱保留率与混押协同增益断言**：构造单项极端谱面，断言主项星级保留率 $\ge 95\%$；构造同星级多维复合谱面，断言综合星级严格大于单项极大值（协同增益 $> +0.8★$）；
  4. **跨平台确定性校验**：相同谱面输入必须输出完全一致的 `star_rating`（精确至 4 位小数）。

### 2. 最高核心受测接缝 (Highest Testing Seam)

系统设立单一顶层测试接缝：
`evaluate_intrinsic_difficulty(content_or_path: Union[str, Path, Beatmap7K], options: Optional[DifficultyOptions] = None) -> IntrinsicDifficultyResult`

及其 CLI 命令行镜像：
`python3 -m proj7k.difficulty <beatmap.osu>`

所有外部集成测试、CI 守卫与算法单调性验收，均无一例外通过该顶层函数调用发起。

### 3. 先验测试参照 (Prior Art)

- `prototypes/prototype_difficulty_engine.py`：作为本规格书的核心数学原型，已证实双手解耦衰减、P90 分位数池化与 tanh 软上限压缩在 120 首标杆曲目上运行良好（$\rho = 0.9884, \tau = 0.9548$）。
- `tests/test_monotonicity_guard.py`：为 CI 阻断与倒挂检测提供了标准守卫测试模型。
- `tests/test_distillation.py`：为 8 维雷达与正交质心分离度验证提供了测试固件与断言标准。

---

## Out of Scope

1. **真实玩家击键回放（`.osr`）分析与个人表现评分 (pp)**：本期仅计算铺面固有的 Star Rating 与能力雷达，严禁混入任何玩家命中率（Acc）、不稳定度（UR）或个人得分数据（严格遵守 ADR-0001）。
2. **Web 检索前端界面与在线数据库搭建**：本期仅输出标准本地数据结构、JSON 报表与 CLI 工具，不涉及任何 React/Vue 前台页面及后端数据库部署。
3. **针对 4K、6K、8K 或非 osu!mania 音游模式的兼容**：系统严格专为 7K 设计，不适配任何非 7K 模式。
4. **实时游玩挂接 (Real-time In-game Overlay)**：系统不以任何内存注入或游戏挂钩形式运行，纯文本离线解析计算。

---

## Further Notes

1. **与 Wayfinder 主图 #2 的承接关系**：本规格书标志着 Wayfinder Issue #2（方法论探索阶段）正式收敛，正式启动 Phase 2 实施交付，并在 GitHub Issues 作为父规格工单发布。
2. **软上限压缩的艺术平衡**：引入双曲正切软压缩（$\tanh$）将极限天花板约束在 12.5★，不仅符合高水平玩家对“240BPM 反键依然处于人类可通过边缘”的心理认知，也为未来更夸张的超限界谱面预留了连续演进的数学空间。
