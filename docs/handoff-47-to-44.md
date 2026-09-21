# 交接：#47 已完结，#50 是入场券，#44 是终点

> 生成于 2026-09-22。上一份根目录 `HANDOFF.md` 是 2026-09-14 的旧session记录，其中第 4 节已被标注过期；本文不替换它，只描述 #47 之后的状态。

## 1. 现在在哪里

```
HEAD                 84b4da3  refactor(radar): separate the technique drivers and revise K_base (#47)
current_engine_version()  ve95640f1
pytest               574 passed / 8 failed
proj7k.batch --guard FAILED：仅 [Regular Speed] star_rating τ 0.809 < 0.88、ρ 0.911 < 0.95
```

**8 条红灯不是待修的 bug，而是「第二步提交」尚未发生**：驱动层（8 维算子）刚被 #47 改对，星级映射没跟上。

| 红灯 | 归属 |
| :--- | :--- |
| `test_120_benchmark_guard` ×3（阶梯单调性 / 星级指纹 / 聚合验收线） | #50 重标定后重基线化 |
| `test_ln_alignment_diagnosis` ×2、`test_tech_alignment_diagnosis` ×3 | `dominant_technique` 期望值；若干是顶到 `score_ceileng=12.0` 的平局，重标定后还会变，届时逐条判定 |

## 2. 下一步的次序：先 #50，再 #44

**不要跳过 #50 直接做 #44。** #44 要求「最终必须一次性切换，避免一半绝对一半相对的中间态」，而切换的支点——按技法分别拟合的 `a / exp / b`——正是 #50 要建的东西。顺序做反了要返工两遍。

### #50：逐技法语义标定（`ready-for-agent`）

第一件事是**工具**。`tools/calibration_sandbox.py` 冻结的正是驱动层（特征 + 应变 + 8 维驱动），而这一层刚变过，冷核心必须重建：给它补一条 `--rebuild` 的冷路径，或新写一个按技法在各自 15 级梯队上拟合 `a / exp / b` 的工具。

工具就位后，目标是 `MAE ≤ 0.65`、`|bias| ≤ 0.3`（对 `docs/research/structured_index.json` 的逐谱 `sr` 真值），同时四档锚点与 15 级单调性不能破。

### #44：雷达分数改为绝对技法星级

入场条件已经满足：#39 / #40 / #41 / #43 均已关闭，#47 已完结。切换后会移动全部星级，触发 ADR-0014 的重评。

## 3. 动手前必须知道的四件事

**① 安全窗很窄，且上行更窄。** `docs/methodology/calibration-sensitivity-and-sandbox.md` 第 5 节：0th / 5th / 10th 三档的上行余量只有 **~6%**，下行 10%–20%。任何把标尺整体抬高超过 6% 的改动必定撞带，「改得对不对无关」。动手前先用沙盘看一眼新点位。

**② 真正的闸门是锚点带，不是秩相关。** 同文第 4 节：τ / ρ 在扫描中几乎纹丝不动（P90 单调 → 阶梯形状天然抗扰动），PASS/FAIL 基本由四个锚点中位数决定。第 7 节另有一条：τ 的 0.019 分辨率是**一个倒挂对**，不要拿它当改善依据。

**③ AC1 与 AC2 方向相反，别指望同时铆死。** #47 的实证：把弱轴抬到能赢自己的梯队，同谱次高维随之被推高（中位 0.719 → 0.820）。四条 LN 梯队在特征空间里本就重合（General 与 Release 在 10th / Azimuth 上是孪生谱）。若要 AC1，须先给出**新的判别量**，而不是调阈值或对分值做单调变换——后者只改度量、不加信息。

**④ 两处 ADR 边界。** `docs/adr/0008-...md` 修订 1 记录了 `K_base` 去掉 speed 的决策与代价（`tech_saturation_gain` 0.15 → 0.8 是配套的，不要单独回退其中一个）。`ln_general` 的密度定义经确认**没有问题**，不要动。

## 4. 立即可用的东西

```bash
# #47 的 120 谱复查表（分离度 / 本维命中率 / 交叉相关 / argmax 分布，带阈值判定）
PYTHONPATH=src python3 tools/radar_orthogonality_report.py
PYTHONPATH=src python3 tools/radar_orthogonality_report.py --json

# 标定沙盘（驱动层冷核心需要重建，见上）
PYTHONPATH=src python3 tools/calibration_sandbox.py
PYTHONPATH=src python3 tools/calibration_sandbox.py --sweep

# 两道闸门
PYTHONPATH=src pytest -q
PYTHONPATH=src python3 -m proj7k.batch --manifest docs/research/structured_index.json \
  --corpus tests/fixtures/benchmark_corpus.json.gz -j 4 --guard
```

**新增的可复用接缝**（#47 留下的）：

- `features.isolated_tail_count` / `isolated_tail_share` — 尾点那一刻无其它键按下的尾放比例
- `features.release_lock_depth` — 每次抬手时同手仍锁着几键的均值（纯形状量，不随段位漂移）
- `radar.compute_raw_technique_drivers(...).to_dict()` — 8 维原始驱动，与星级映射分离的独立接缝
- `tools/radar_orthogonality_report.py` — 判断「这一版驱动到底分不分离」的量尺

## 5. 纪律提醒

- **「两步提交」**：先改常数，确认新阶梯正是想要的，再重新基线化星级指纹与验收线（`AGENTS.md`）。#47 只做了第一步，第二步留给 #50。
- **字面量登记守卫**：`tests/test_engine_literal_registry.py` 要求星级路径上每个函数体内的数值字面量逐值逐次登记。新增算子时它会挡下来，这是它存在的意义，不要绕过。
- **空旋钮守卫**：任何进了指纹却不被任何算子读取的选项字段都会被 `test_every_fingerprinted_option_field_is_read_by_an_operator` 揪出（#47 期间正是它发现了 `ln_release_rate_weight` / `ln_release_antiphase_weight` 两个孤儿）。
