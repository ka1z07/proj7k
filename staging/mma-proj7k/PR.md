## 概述

新增 7K 专用估算器 **Proj7k**：把 [proj7k](https://github.com/ka1z07/proj7k) 的 7K 难度引擎（规格 v0.2.3，上游 `915e5af`）移植为 DOM-free 的 JS 模块，作为一个新的算法选项接入。写法参照 Polaris：只对 7K 生效，其他键数与 IN/HO 转换图回退 Sunny；星数胶囊仍显示 Sunny sr。

## 改动

- `js/estimator/proj7k/engine.js`：引擎本体。按手指建模每个音符对手的负荷（同指重复、同手其他手指的牵连、另一只手的干扰、按住长条对同手的约束），每只手累积随时间衰减的负荷，再求「期望失误刚好等于容忍度时的玩家水平」作为整首难度；另对 8 秒窗口单独求解，避免短爆发被平均掉。技法层给出 8 个技法（RC jack/tech/speed/stamina，LN general/tech/inverse/release）各自的星数与主导技法。
- `js/estimator/proj7kEstimator.js`：把引擎星数按锦锦 7K Regular 段位 15 档的星数，在 ln(star) 上插值成连续 level（0 = Regular 0 … 14 = Regular Stellium，编号同 Polaris），输出 `estDiff`（如 `Regular 6 mid/high`）与 `numericDifficulty`。引擎只有一条段位刻度，因此不另给 LN 半标签。引擎星数、拇指手、主导技法与 8 个技法的读数放在 `debug`。
- 接入：`settings.json` / `config.js` / 设置解析 / 管线分派 / 遥测白名单（前端与 `backend/internal/spec/spec.go` 同步）。
- 文档：新增 `docs/proj7k_algorithm.md`（英文），并同步 README、docs 索引、难度估计、设置、遥测与管线文档。

## 验证

- 与上游 Python 引擎在其 120 张锦锦 7K 标杆谱上逐张对比：总星数与 8 个技法星数最大差 4e-15，技法 level 最大差 1e-15，拇指手与主导技法全部一致。Node 22 下每张约 40 ms。
- Node 下跑完整 `runAnalysisPipeline`：7K 谱 `actualEstimatorAlgorithm = "Proj7k"`；4K 与 HO 转换谱回退 Sunny；1.5x 倍速读数按预期升高。
- 未做：浏览器内实机界面测试；VSRG-DanEstimation-Benchmark 目前只有 4K 样本，Proj7k 在其上全部回退 Sunny，无法用它验证。
- 难度反馈（desktop feedback）未接入 Proj7k，7K 反馈仍以 Polaris 为目标。
