# 8 维技法驱动的正交性实测（工单 #47 复查表）

> 由 `PYTHONPATH=src python3 tools/radar_orthogonality_report.py` 生成，数据源为
> `docs/research/structured_index.json` × `tests/fixtures/benchmark_corpus.json.gz`（120 谱）。
> 生成于工单 #52 阶段①（ln_inverse 重构、Rule E 顺序、ln_release 仿射式 + lockd 定义修正）之后。
> 分离两阈值的现状已被票主接受为阶段② 起点，CI 以棘轮守住不再恶化。
> 手工改动本节数字没有意义——重跑工具。阈值的 CI 强制在 `tests/test_radar_orthogonality.py`。

```
charts: 120

=== separation (second-highest / highest driver) ===
median                    0.8076   (threshold <= 0.5)
charts > 0.8             61   (threshold <= 10)
charts > 0.5               88

=== per group ===
group            own axis       hits  median sep
Regular Jack     jack          14/15      0.0281
Regular Tech     tech           9/15      0.7674
Regular Speed    speed         14/15      0.5890
Regular Stream   stream        12/15      0.8200
LN General       ln_general    11/15      0.8764
LN Tech          ln_tech        7/15      0.8696
LN Inverse       ln_inverse    12/15      0.3009
LN Release       ln_release     7/15      0.8739

=== argmax distribution ===
jack 16  tech 14  speed 17  stream 17  ln_general 26  ln_tech 7  ln_inverse 12  ln_release 11  None 0

=== cross-correlation (Spearman rho over the corpus) ===
                       jack       tech      speed     stream  ln_genera    ln_tech  ln_invers  ln_releas
jack                  1.000     -0.259     -0.101     -0.059     -0.754     -0.655     -0.768     -0.755
tech                 -0.259      1.000      0.701      0.726      0.253      0.224      0.181      0.252
speed                -0.101      0.701      1.000      0.853     -0.031     -0.019     -0.106     -0.033
stream               -0.059      0.726      0.853      1.000      0.084      0.070     -0.016      0.080
ln_general           -0.754      0.253     -0.031      0.084      1.000      0.906      0.896      0.998
ln_tech              -0.655      0.224     -0.019      0.070      0.906      1.000      0.686      0.886
ln_inverse           -0.768      0.181     -0.106     -0.016      0.896      0.686      1.000      0.906
ln_release           -0.755      0.252     -0.033      0.080      0.998      0.886      0.906      1.000

Radar orthogonality: FAILED
  - median separation 0.8076 > 0.5 (axes are still coupled)
  - 61 charts above 0.8 > 10
```
