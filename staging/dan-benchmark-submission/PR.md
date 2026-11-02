# 新增算法结果：proj7k / Add results: proj7k

**中文 | English below**

你好！我们是 [proj7k](https://github.com/ka1z07/proj7k)（MIT 许可），一个 osu!mania **7K** 难度引擎。我们按 `docs/runner.md` 跑了这份基准，想把结果提交上来，也顺便介绍一下引擎的思路。先说清楚两点：

1. **引擎是为 7K 设计的，没有为 4K 改过任何参数。** 我们只是把 4K 的 0/1/2/3 列放到引擎 7K 手型的 1/2/4/5 列上（左中指、左食指 | 右食指、右中指），然后直接运行。
2. **星级 → 数值化难度的换算表是用本基准拟合的。** 我们的星数用的是 Jinjin 7K 段位的刻度，和 Reform 4K 没有现成的对应关系，所以用全部 1349 行拟合了一条保序回归（PAVA）曲线（162 个节点，见 `star_to_dan_isotonic.json`），做法和 Azusa 的 84 点保序表一样。因为是在同一份数据上拟合的，表上的数字会偏乐观。下面同时给出 **10 折交叉验证**的数字（每张谱面的换算曲线都没有见过这张谱面），我们认为那一列更能代表真实水平。

## 结果

`got` 来自上面那张固定的换算表。所有 1349 行都有数值（`Invalid`/`Missing`/`Failed` 都是 0）。

| 范围 | 行数 | MAE（提交的 CSV，样本内） | MAE（10 折交叉验证） |
|---|---|---|---|
| 全部 | 1349 | 0.304 | 0.338 |
| AquaCrest/Azusa/ISOR/Sunny/Companella/Mixed/P1h 都有效的行 | 1245 | 0.250 | 0.278 |
| 9 个算法都有效的行 | 1104 | 0.203 | 0.220 |
| RC 11–17 | 1090 | 0.198 | 0.213 |

按键型（样本内 / 交叉验证）：jack 0.26 / 0.29，speed 0.23 / 0.25，stamina 0.24 / 0.26，tech 0.24 / 0.27，course 0.47 / 0.54，ln 0.96 / 1.07。

已知的弱项：
- **4K LN 读低约 1 段**。这在很大程度上是有意的：我们的引擎在设计上就把 LN 的读数压得比 RC 低。另外 LN 段位和 Reform 是两套刻度，一条共用曲线本来就对不齐（Mixed 和 Companella 在 LN 上也偏低约 1.0）。
- **9 段以下偏高**，因为低段样本少，曲线在那里比较粗。
- 段位课程整体排序很好（34 首课程 Spearman ρ 0.997），但长课程的读数偏高约 0.5。

如果你们觉得「换算表用了基准本身」不适合放进排行，也可以只把交叉验证的数字当作参考，或者在网页上标注一下。怎么处理都听你们的。

## 算法简介

完整规格见仓库里的 [`osu-mania-7k-difficulty-spec.md`](https://github.com/ka1z07/proj7k/blob/main/osu-mania-7k-difficulty-spec.md)，代码在 [`src/proj7k/engine/`](https://github.com/ka1z07/proj7k/tree/main/src/proj7k/engine)。

整体框架分成五层，从单个音符一路推到整首谱的星数：

```
谱面 → 指法映射 → 逐事件的身体负荷 → 手部负荷累积 → 阈值求解（整首难度）→ 星标尺
                                                 └→ 技法拆分（各技法难度）
```

**1. 以手指为单位建模。** 每一列对应一根具体的手指，按下和松开都算作事件。难度来自手的物理限制：同一根手指要多快地重复按，同一只手里其他手指的动作对它有多少牵连（相邻手指比远端手指更难分开），另一只手有多少干扰，以及按住长条时对同手其他手指的约束。我们不直接看「每秒多少个音符」，而是看「这些音符落到手指上之后，身体要承受多少」。

**2. 速度有绝对刻度。** 身体的代价随速度非线性增长：接近手指极限时代价上升得比速度本身更快。所以同一个键型放快以后，读数会超线性变难，而不是简单地按比例放大。

**3. 负荷累积。** 每只手维护一个随时间衰减的负荷，事件难度读的是「这只手当前持续承受的负荷」，而不是单个音符的瞬间峰值。

**4. 难度 = 阈值。** 这是和大多数算法最不一样的地方。我们不对局部难度取分位数或加权平均，而是问：**一个多强的玩家，在这张谱上的期望失误刚好落在可容忍的范围内？** 把每个事件的失误概率写成「玩家水平」和「事件难度」的函数，整首谱的难度就是让期望失误等于容忍度的那个水平。这样短而难的段落和长而稳定的负荷能放在同一个口径下比较（思路上和 P1h 的「打到 96% 所需水平」最接近）。在此之上，再对每个短窗口单独求一次，保证只占一小段的高密度爆发不会被整首谱平均掉。

**5. 星标尺靠少数人工锚点。** 难度值到星数是一条两参数的曲线，只用几条人给定的锚点来定（「Jinjin 7K 某段的谱面大约几星」）。引擎其余部分都是按物理直觉设定的先验，**没有在大规模数据上拟合**。验收靠的是 Jinjin 7K 段位各技法池的阶梯单调性，另外有一小批外部留出谱只做检验。

**6. 技法拆分。** 同样的阈值求解，按每个事件的特征把它分配到 8 个技法池（RC jack/tech/speed/stamina，LN general/tech/inverse/release），分别求出各技法的难度和主导技法。这一层是按 7K 设定的，在 4K 上不适用（4K 每只手只有两列），所以这次只提交总难度。另外，引擎在设计上有意把 LN 的读数压得比 RC 低。

## 复现

```bash
git clone https://github.com/ka1z07/proj7k && cd proj7k   # commit c544e65
pip install numpy scipy
PYTHONPATH=src python3 proj7k_runner.py <本基准仓库路径> star_to_dan_isotonic.json
```

`proj7k_runner.py` 和 `star_to_dan_isotonic.json` 放在这个 gist 里：GIST_URL（不放进 `results/`）。在 4 核机器上全部 1349 张大约需要 100 秒。倍速固定 1.0，谱面匹配按 精确 → 标准化 → `Name (...)` 的顺序。

## 7K 样本

README 里提到以后可能支持 4K 以外的样本。如果以后要做 7K，我们很乐意提供自己的 7K 验收集清单（Jinjin 7K 段位，8 个技法池 × 15 档，共 120 张，含谱面 ID 和段位）。

谢谢维护这个基准！

---

## English

Hi! We are [proj7k](https://github.com/ka1z07/proj7k) (MIT), an osu!mania **7K** difficulty engine. We ran this benchmark following `docs/runner.md` and would like to submit the results. Two things up front:

1. **The engine is built for 7K and has not been tuned for 4K.** We place 4K columns 0/1/2/3 on the engine's 7K columns 1/2/4/5 (left middle, left index | right index, right middle) and run it unchanged.
2. **The star → numeric mapping is fitted on this benchmark.** Our stars are on a Jinjin 7K dan scale, so we fit one isotonic (PAVA) curve on all 1349 rows (162 knots, `star_to_dan_isotonic.json` in GIST_URL, together with the runner `proj7k_runner.py`), like Azusa's 84-point isotonic table. Because it is in-sample, the submitted numbers are optimistic. We also report **10-fold cross-validated** figures, where each chart's mapping never saw that chart; we think those are the fairer numbers.

**Results.** All 1349 rows are numeric. MAE, in-sample / cross-validated: all rows 0.304 / 0.338; the 1245 rows valid for AquaCrest, Azusa, ISOR, Sunny, Companella, Mixed and P1h: 0.250 / 0.278; the 1104 rows valid for all nine algorithms: 0.203 / 0.220; RC 11–17: 0.198 / 0.213. Known weak spots:
- 4K LN reads about one dan low. This is largely by design: our engine intentionally reads LN lower than RC. LN dans are also a separate scale.
- Below dan 9 reads too high (few samples there).
- Long courses read about 0.5 too high, although the course order is near perfect (ρ 0.997).

If an in-sample mapping is not appropriate for the leaderboard, please treat the CV numbers as the reference or flag the entry. Your call.

**How the engine works** (full spec: `osu-mania-7k-difficulty-spec.md`; code: `src/proj7k/engine/`). The design has five layers:

```
chart → finger mapping → per-event physical demand → per-hand load → threshold solve (chart difficulty) → star scale
                                                                   └→ technique split (per-technique difficulty)
```

1. *Model fingers, not columns.* Each column is a specific finger, and presses and releases are events. Difficulty comes from the hand's physical limits: how fast a finger must repeat, how much the other fingers of the same hand interfere (neighbours are harder to separate than distant fingers), the other hand's interference, and the constraint a held LN puts on the rest of that hand.
2. *Speed has an absolute scale.* Physical cost grows faster than speed near a finger's limit, so the same pattern played faster reads super-linearly harder.
3. *Load.* Each hand carries a decaying load, so an event reads the hand's sustained load rather than an instantaneous peak.
4. *Difficulty as a threshold.* This is the main difference from most algorithms. Instead of taking percentiles or power means of a strain curve, we ask how strong a player must be for the expected misses on the chart to fall exactly at a tolerated level. Each event's miss probability is a function of player level and event difficulty, and the chart's difficulty is the level that solves this. It is closest in spirit to P1h's "level needed for 96%". Short windows are also solved separately, so a short dense burst is not averaged away.
5. *A star scale set by a few human anchors.* A two-parameter curve from difficulty to stars is fixed by a handful of statements of the form "charts of this Jinjin 7K dan read about S stars". Everything else is a physically motivated prior, not fitted on a large dataset. Acceptance is ladder monotonicity across the Jinjin 7K technique pools, plus a small external hold-out set.
6. *Technique split.* The same solve, run under per-event memberships for 8 technique pools (RC jack/tech/speed/stamina; LN general/tech/inverse/release), gives per-technique difficulty and the dominant technique. This layer is 7K-specific and does not transfer to 4K, so only the total is submitted. The engine also reads LN lower than RC by design.

**7K samples.** If 7K samples are added later, we are happy to share our 7K acceptance list (Jinjin 7K dans, 8 technique pools × 15 tiers, 120 charts with beatmap IDs and tiers).

Thanks for maintaining this benchmark!

---
_Generated by [Claude Code](https://claude.ai/code)_
