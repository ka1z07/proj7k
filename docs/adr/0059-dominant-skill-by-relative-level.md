# 0059. 主导技法改为相对同总星期望的水平，π 退为诊断

- **状态**：accepted（2026-11-05；规格修订记录 v0.2.4 第 11 项；修订 ADR-0017 的主导度读法；总星级、技法星级、阶梯全部不动；引擎版本号随代码变动，目录要重摄入、lazer 要重同步主导技法标签）。
- **背景**：Kai 2026-11-05 给出 speed 与 stamina 的分法——任何 speed 谱把 BPM 放慢都是 stamina；一张谱被认作 speed，是因为在它的总难度与总密度下所需的速度比同难度的谱高得多；和弦密度上去就逐渐变成 stamina。ADR-0056 之后技法星级已经是绝对的「要求有多高」，这个定义说的是**标签**：标签是相对量，技法星要和总星一起读。此前主导技法按 $\pi_k$（总难度由谁决定）：Stream 池 0th–6th 全读 speed、3rd/8th 读 jack，Regular Tech 只有 5/15，100 张 rank 图里主技法 ≠ 最高星技法的有 60 张（ADR-0056 补记）。

## 决策

1. **技法水平 $\ell_k=\star_k/E_k(\star)$。** $E_k$ 是技法 $k$ 所属家族（rc_* 用四个 Regular 池、ln_* 用四个 LN 池）60 张标杆谱上 $\ln\star_k$ 对 $\ln\star$ 的最小二乘直线，钉在 `engine/scale.py` 的 `SKILL_EXPECT`，`tools/fit_skill_scale.py` 连同 `SKILL_SCALE` 一起重拟合，`tests/engine/test_engine_skills.py` 复核。$\ell_k=1$ 是「同总星的谱通常就要求这么多」，1.2 是高出两成。
2. **家族先于技法。** 谱面是 LN 谱当且仅当 §8.1 的 $\bar\lambda\ge 1/2$（120 张全对；按 $\pi$ 的家族份额判有两张 LN Tech 谱错判）。另一家族的四个技法排在本家族之后。
3. **家族内按水平排位，LN 伞型按常数参与。** 排位数 $r_k=\ell_k$，`ln_general` 取 `umbrella_level` = 1.05：没有专项站到期望的 1.05 倍以上时由它持有。RC 家族没有伞型。主导技法为 $\arg\max r_k$，`dominance_rank`、`dominance_margin` 按 $r_k$。
4. **$\pi_k$ 保留**（`skills[k].dominance`），仍回答「总难度由谁决定」，§14 T6/T10/T11 不变；`Params.dominance_model = "pi"` 保留旧规则，`V02` 用它，金标准测试不动。
5. **下游只换语义不换接口。** 输出结构多一个 `level`；降阶器的主导技法守恒门禁改比较 `profile.dominance_key`，平局阈值 `DOMINANCE_TIE` 0.03 → 0.05（与伞型的 5% 同一个数），并多一条「原主导技法在练习谱里相对其总星的水平不比原谱低 0.05 以上」也算守恒——纯删音会让 rc_tech 的读数几乎不动而总星下降，于是每次降阶 tech 的水平都机械上升（Stream Stellium 降到 7th：tech 0.76 → 1.09，stamina 1.05 → 1.04；Jack Gamma 降到 5th：tech 0.89 → 1.05），只按「谁最高」会让 Stream/Speed 高段位谱在 8th 以下停住；练习谱要守的是「仍然要求它要练的技法」；目录 `skills_json` 带 `level`，谱面页在主导度旁显示水平；`dominant_skill` 的所有读者（目录、lazer 标签、降阶器、画像）不改代码。

## 实测（120 张标杆谱 + 19 张留出整曲）

| 池 | π 规则 | 本决策 |
|---|---|---|
| Regular Jack | 14 | 14 |
| Regular Tech | 5 | 11 |
| Regular Speed | 15 | 13 |
| Regular Stream | 7 | 15 |
| LN General | 12 | 9 |
| LN Tech | 4 | 5 |
| LN Inverse | 3 | 12 |
| LN Release | 6 | 12 |
| 合计 | 66/120 | 91/120 |

`tests/engine/test_engine_skills.py` 的 `DOMINANT_FLOOR` 按池棘轮钉住这些数。Speed 池 speed★ 在 15 级全部高于期望 +5%～+15%，Stream 池 speed★ 均值 0；stam★ 则 Stream 池 +3%～+18%、Speed 池 −30%～+9%。Speed 池掉的两张：9th（Mario Paint，叠键 1.96、87 s 不休，stamina 水平 1.08 > speed 1.06，用 7k-VSDL 推理也读 stamina 略多）与 Stellium（tech）。

19 张留出整曲：9/19（π 规则 8/19），前二 12/19。整曲的 rc_tech 水平普遍 1.05–1.10（12 little fragments、Vortex of Sorrow、Inorganic Dystopia 都被读成 tech）——整曲的节拍分型多样性比段位切段高，期望是在切段上拟的；这是已知缺口，留给 tech 判别量的下一轮。

伞型阈值 1.0–1.15 之间 LN 合计 36–38/60，取 1.05（LN General 9、LN Tech 5）；1.2 以上 LN Tech 归零。

## 试过未采纳

- 把相对量放进 speed 的代价（谱内「爆发速率 / 持续速率」Speed 池 1.3–3.0、Stream 池 1.1–1.4 分得开，但沿段位是平的，会把 Speed 阶梯打平）。
- 全 120 张一起拟合期望：LN 技法在 RC 谱上为 0、直线斜率为负，LN 池合计 5/60。
- 不设伞型、ln_general 直接参与 argmax：LN General 池 3/15。
- 主导技法 = 最高技法星：Jack 12、Tech 11、Speed 10、Stream 11、General 7、LN Tech 9、Inverse 3、Release 6（70/120），LN 侧比 π 还差。

## 后果

- 目录里每张谱的 `dominant_skill`、`dominance_margin`、`skills_json` 都会变，重摄入即可；lazer 的 `dominant_*` 标签与收藏夹要重同步。
- 降阶器的「主导技法守恒」现在守的是相对水平：一张 Stream 谱降阶后仍要「相对同总星像原谱一样偏 stamina」，不再是「π 份额不变」。已知缺口：删音生成的节奏空洞让 rc_tech 水平机械上升，降阶器下一轮要按整行/整段删而不是逐音删。
- 画像（ADR-0057）不读主导技法，不受影响。
