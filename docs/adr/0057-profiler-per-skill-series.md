# 0057. 画像的技法水平改在技法自己的序列上反解

- **状态**：accepted（2026-11-03；修订 ADR-0020 决策 2 与 ADR-0028 的逐技法一侧；引擎未动；快照读法版本 `SNAPSHOT_INGEST_VERSION` 4 → 5）。
- **背景**：ADR-0056 把技法星级改成「这张谱对技法 k 的要求有多高」，每个技法有自己的代价序列 $c_{ik}$、读数 $d_{ik}$ 与星标尺。画像的逐技法水平（`profiler/response.py`）却还在**总需求** $d_i$ 上、以成员度 $w_{ik}$ 加权反解玩家水平：它报告的 `chart_level` 已经是技法自己标度上的 $D_k$，玩家水平却还在总标度上，两者不同尺；`field.skill_demand`（致命段的技法峰值）和 `aggregate.py` 把水平换成星级的三处也都读的是总标尺。更根本的是，成员度加权的反解回答的是「玩家在带有该技法色彩的事件上的总水平」，不是「玩家的该技法有多高」。

## 决策

1. **逐技法水平在 $d_{\cdot k}$ 上反解，单位权重。** 对技法 k，取该局已打到的、$d_{ik}>0$ 的事件（`played * (d_k > 0)`），用同一条损失模型以该技法的 $\varepsilon_{\text{family}}$ 反解水平 `level_for_loss(d_k, w, observed, eps)`；星级用 `scale.skill_stars_of(name, level)`。`chart_level` 仍是 $D_k$，现在与玩家水平同尺。`bins` 也按 $d_{\cdot k}$ 分箱。
2. **只在谱面确实要求该技法时才算测到。** 技法 k 的谱面星级低于总星级的 `min_skill_share = 0.7` 倍，就记 untested：一张 Jack 谱上 rc_speed 的读数来自叠键之间零星的移动，拿它反解玩家的 speed 是把噪声当测量。0.7 来自标杆谱的实测：本池技法在本池上的星级 / 总 ≥ 0.92，而 Jack 谱上 rc_tech 读到总的 0.69（0.5 挡不住）。主导技法与短板只在测到的技法里选。
3. **汇总与致命段同尺。** `aggregate.py` 的拟合水平、中位数回退、致命下限三处换算都走 `_stars_of_key(dim, level)`（技法自己的标尺）；`field.skill_demand` 的峰值读 `dk[:, k]`。
4. **快照版本 +1。** 已存的快照 `ingest < 5` 视为过期，导入回放时重读；Kai 合并后要重跑一次「导入回放」。

## 后果

- 单局反解的斜率仍是引擎的 β（ADR-0028 的跨局拟合不变，只是输入换成了技法自己的读数）。
- 老快照里的逐技法水平不可比：它们是总标度上的读数。画像页在重导入之前会把它们当过期快照忽略。
- 测到的技法变少了（每局通常 1–3 项），画像的逐技法曲线会更稀但更可信；`MIN_FIT_PLAYS` 不变。
- 测试：`tests/test_profiler_response.py`（inverse ≤ 干净局、release < inverse、Jack 谱上 speed 不测、tech 只在份额 ≥ 阈值时测）、`test_profiler_capacity_snapshots.py`、`test_profiler_profile_fit.py` 改成技法标尺的期望。
