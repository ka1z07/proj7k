# 0056. 技法层改为逐技法代价

- **状态**：accepted（2026-11-03；规格修订记录 v0.2.3；修订 ADR-0017 的技法读法、ADR-0019 的本池闸门、ADR-0051 的按池收益；总星级不动）。
- **背景**：Kai 问「从根本和原则性上思考我们的技法难度拆分上有什么缺陷」。审视结论（`/mnt/project-files/mania-hub-7k/skill-split-critique.md`）：v0.2 的八个技法难度都是**同一条**事件需求 $d_i$ 在不同成员权重下的解（$D_k=\mathrm{solve}(d, w_{\cdot k})$），而 $d_i$ 是一只手四秒记忆的读数，加权上分位数几乎不随权重变——120 张标杆谱上每个技法星级都是总星级的 0.82–1.00，Regular Speed 10th 的 rc_jack 覆盖只有 0.13、rc_jack 星级却读 7.29（总 7.99）。本池技法在家族内最高的只有 4–13/15（Tech、LN Tech 各 4/15）。拆分活在权重里而不在代价里；只有 jack 有自己的物理量；节奏意外只进成员度不进需求；成员度是形状代理（speed = 非叠非和弦的残差）；护栏只查本池单调，扁平的技法谱面免费通过。Profiler 的逐技法水平（ADR-0028）、降阶器主导技法、pool-range、目录全都继承这份扁平。
- **Kai 的三条裁定**（2026-11-03）：技法星级的语义是「这张谱对技法 k 的要求有多高」；八个池名只是输出标签，引擎内部的能力基可以不同；总星级冻结，技法层单独做。随后：「等 8 个站得住再合并，降阶器的门禁如果还能沿用就用呗」。

## 决策

1. **总星级不动。** `hand_demand` 的 $v$、$d$，求解器，`STAR_A/B`，`anchors.json`，星级指纹，四档区间，爆发窗口，全部不碰。技法层另起一层（`engine/skillcost.py`），只读 $v$ 的组成部分与事件属性。120 首星级指纹与金标准不变。
2. **每个技法有自己的代价序列 $c_{ik}$，而不是自己的权重**（规格 §8.3）。$d_{ik}$ 是同一只手累加器以 $c_{ik}$ 为输入、记忆 $\tau_k$ 的读数，$D_k=\mathrm{solve}(d_{\cdot k},\,w\equiv 1,\,\varepsilon_{\text{family}})$——解法器、累加器、损失函数都不变，变的只是输入。没有该技法事件的谱读 0。八条代价（$\lambda$ 为 §8.1 的 LN 语境）：

   | 技法 | $c_{ik}$ | 含义 |
   |---|---|---|
   | rc_jack | $v$ 的同指部分（$k_{\text{same}}\,j\cdot\text{rise}$ + minijack）$\times(1-\lambda)$ | 手指重复自己 |
   | rc_speed | $v$ 的邻指 + 跨手部分（$(x+o)\,c\cdot\text{rise}$）$\times(1-\lambda)$，记忆 $\tau_{\text{speed}}=1.5$ s | 一只手整体的移动速率（爆发） |
   | rc_stamina | $v\times\min(1,\,\text{本手自上次休息以来的秒数}/20)\times(1-\lambda)$ | 持续不休的负荷（休息 = 本手空档 > 0.6 s） |
   | rc_tech | $v\times(0.25+0.75\,q_{\text{irr}})\times(1-\lambda)$ | 不规则下的负荷：$q_{\text{irr}}=\max(q_U,\,0.7\cdot[\text{本手间隔不是前一间隔的可承接比}],\,q_\delta)$，$q_\delta$ 是节拍分型多样性（事件前后各 1 s 内行间隔对其中位数之比归成的类数，占比 ≥ 10% 的类才算，1 类读 0、3 类读满；社区对 tech 的定义就是「混用节拍分型、复节奏」） |
   | ln_general | $v\times\lambda$ | LN 语境下的全部负荷 |
   | ln_tech | $v\times(0.25+0.75\,q^{\text{LN}}_{\text{irr}})\times\lambda$ | LN 语境下的不规则；$q^{\text{LN}}_{\text{irr}}$ 不含 $q_\delta$（LN 谱上多样性数的多是错开长条的头，带上它 LN Tech 阶梯 τ .886→.867、本池最高 13→12） |
   | ln_inverse | $v\times\min(1,\,h/2)^2\times\lambda$（仅按压，$h$ 为两手按住的列数） | 按住时的并发 |
   | ln_release | $v\times[\text{尾在无按压行}]\times(0.2+0.8\min(1,\,0.3/\ell))\times\lambda$ | 孤立松键，短 LN 的尾更要求时机 |

3. **逐技法星标尺**（规格 §10）。$S_k=a_k D_k^{b_k}$，$(a_k,b_k)$ 为本池 15 档 $D_k$ 中位数对**冻结的家族总星级阶梯**（RC：四个 Regular 池总星级的档中位数；LN：四个 LN 池的档几何平均，`test_external_holdout.py` 的定义）的 ln–ln 最小二乘。「rc_tech 7.0★」读作「要求第 7 段的 tech」，和总星级同一刻度。`scale.SKILL_SCALE` 钉住八对；`tools/fit_skill_scale.py` 是拟合，`tests/engine/test_engine_skills.py` 重跑它。总标度是权威，技法标度随它与各技法代价重拟合。
4. **成员度与主导度保留为诊断，主导技法不改语义。** $w_{ik}$ 只用于覆盖 $C_k$，$a_{ik}$ 只用于主导度 $\pi_k$ 与 `dominant_skill`。降阶器的技法门禁（`validator`：八个技法星级的余弦 ≥ 0.80 + 主导技法不变）与 `pruner` 的 `DOMINANCE_BIAS` 因此原样沿用，`test_gate_validator`、`test_downscale_engine` 全绿。
5. **验收从「本池单调」改为「单调 + 特异性」。** `tests/engine/test_engine_skills.py` 新增三道棘轮（实测值，只准收紧）：跨家族泄漏（RC 池的 LN 技法 / 总 ≤ 0.15，LN 池的 RC 技法 ≤ 0.65）；特异性（技法 k 在其他池的读数 / 本池同档读数的上限，伞型技法 rc_stamina 0.96、ln_general 1.05 允许接近 1）；本池技法在家族内最高的张数下限。合成谱钉住：纯米谱八个 LN 技法读 0、纯 LN 谱四个 RC 技法读 0；单列叠的 rc_jack > 2× rc_speed。
6. **按两步提交重基线化技法产物。** `guard.OWN_SKILL_RATCHET`、`test_engine_ladder_t1.KNOWN_INVERSIONS`（22 处，v0.2.2 为 8）、`test_120_benchmark_guard` 的「低于素阶梯」池集合（只剩 LN Release）。星级指纹、`dan_table.json`、四档区间不需要动。`params.V02` 以 `skill_model="membership"` 保留旧技法层，金标准 `engine_golden_v02.json` 继续钉旧路径。
7. **下游。** 难度场 `ChartField` 多两组序列 `ck`、`dk`（[n, 8]，各技法自己的代价与读数）。pool-range 降阶（ADR-0051）改在该池自己的序列上读闭式收益（`removal_benefit(field, level, d=, v=, tau=)`），上限/下限按该技法自己的标尺反求成 $D_k$；`field.skill_demand` 与 profiler 的逐技法拟合**暂不动**（见后果）。

## 实测（120 张标杆谱）

- **跨家族泄漏**：RC 池上 LN 技法 / 总 0.00–0.07（v0.2 为 0.82–1.00）；LN 池上 RC 技法 0.21–0.78（LN 谱的米是真实负荷、LN Tech 的米是真实 tech，LN 语境只部分折扣）。
- **特异性矩阵**（技法在其他池的最高相对读数）：rc_jack 0.91、rc_tech 0.90（多样性之前 0.93）、rc_speed 0.90、rc_stamina 0.95、ln_general 1.04、ln_tech 1.03、ln_inverse 0.80、ln_release 0.97。
- **本池技法在家族内最高**：Jack 6→12/15、Tech 4→10、Speed 9→8、Stream 13→11、General 8→7、LN Tech 4→13、Inverse 5→3、Release 6→6。
- **本池阶梯**（τ / 邻档倒挂）：Jack 0.905/4、Tech 0.905/4（只有节奏意外时 0.809/6）、Speed 0.981/1、Stream 0.981/1、General 0.886/3、LN Tech 0.886/4、Inverse 0.962/2、Release 0.771/3。v0.2 的本池阶梯因 $D_k\approx D$ 等于总星级阶梯（τ 0.96–1.0），现在测的是另一个量，倒挂多是正常的。
- 总星级指纹、金标准、段位表、19 张外部留出谱全部不变。

## 后果与已知缺口

- **profiler 的逐技法水平（ADR-0028）**原本仍在旧语义上（总需求 $d$ 上以成员度加权反解），ADR-0057 改到 $d_{\cdot k}$ 上反解、`field.skill_demand` 读 `dk`，快照读法版本 +1。
- **rc_stamina 不特异**（Jack/Tech/Speed 池读 0.92–0.95）：「本手 20 s 不休」在标杆切段里几乎处处成立。原型试过和弦加权（Kai 的「流」标签是单音流，留出谱变差）与拉长到 60 s，都放弃。
- **tech 的判别量**：只有节奏意外 $U$ 时 Regular Tech 阶梯 τ 0.81、高档 Tech 谱的 $U$ 反而低。按社区定义加了节拍分型多样性 $q_\delta$ 后 τ 0.905、对 Speed 的特异性 0.93→0.90；代价是 19 张留出谱里高档 tech 歌（Azimuth、10th）读低两档而不是一档（它们是整曲、多样性被稀释），留出内 ±1 档命中仍 6/19。装饰音（grace）在 Tech 池 3.1× 但无阶梯贡献、未采用；手型切换、跨手错位、反直觉指法仍待进物理层。
- **LN 伞型**：inverse 谱同时是 general 谱（读 1.04），「要求」语义下是对的；ln_release 在 Inverse 池读 0.97（孤立松键在 inverse 谱里天然多），LN Release 阶梯 τ 0.77（该池总星级阶梯本来就弯）。原型试过按住惩罚，把 Release 阶梯打到 τ 0.62，放弃。
- 留出谱里三张 `Code Name Delta [ln_inverse]` LN 占比只有 12–16%，Prism 也不认为是 LN 谱，标注本身可能有问题。
- 运行时间：每谱多八次累加与八次解，约 +0.4 s。
- 目录里的技法星级都变了（总星级没变），重摄入即可；lazer 只写总星级、段位与主导技法（主导度未变），不需要重同步。
