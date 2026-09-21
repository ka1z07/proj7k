# 8 维技法驱动的正交性实测（工单 #47 复查表）

> 由 `PYTHONPATH=src python3 tools/radar_orthogonality_report.py` 生成，数据源为
> `docs/research/structured_index.json` × `tests/fixtures/benchmark_corpus.json.gz`（120 谱）。
> 生成于 `K_base` 修订（ADR-0008 修订 1）与 `ln_release` 重建之后。手工改动本节数字没有意义——重跑工具。

```
charts: 120

=== separation (second-highest / highest driver) ===
median                    0.7207   (threshold <= 0.5)
charts > 0.8             47   (threshold <= 10)
charts > 0.5               97

=== per group ===
group            own axis       hits  median sep
Regular Jack     jack          14/15      0.0281
Regular Tech     tech           9/15      0.7674
Regular Speed    speed         14/15      0.5890
Regular Stream   stream        12/15      0.8200
LN General       ln_general     9/15      0.7736
LN Tech          ln_tech        5/15      0.8671
LN Inverse       ln_inverse     5/15      0.7357
LN Release       ln_release     8/15      0.6446

=== argmax distribution ===
jack 16  tech 14  speed 17  stream 17  ln_general 20  ln_tech 5  ln_inverse 8  ln_release 23  None 0

=== cross-correlation (Spearman rho over the corpus) ===
                       jack       tech      speed     stream  ln_genera    ln_tech  ln_invers  ln_releas
jack                  1.000     -0.259     -0.101     -0.059     -0.724     -0.667     -0.767     -0.768
tech                 -0.259      1.000      0.701      0.726      0.264      0.259      0.170      0.224
speed                -0.101      0.701      1.000      0.853     -0.025      0.004     -0.091     -0.068
stream               -0.059      0.726      0.853      1.000      0.096      0.098     -0.017      0.019
ln_general           -0.724      0.264     -0.025      0.096      1.000      0.935      0.844      0.885
ln_tech              -0.667      0.259      0.004      0.098      0.935      1.000      0.718      0.762
ln_inverse           -0.767      0.170     -0.091     -0.017      0.844      0.718      1.000      0.982
ln_release           -0.768      0.224     -0.068      0.019      0.885      0.762      0.982      1.000

Radar orthogonality: FAILED
  - median separation 0.7207 > 0.5 (axes are still coupled)
  - 47 charts above 0.8 > 10
```
