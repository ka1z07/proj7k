## Agent skills

### Issue tracker

GitHub Issues（基于 `gh` CLI）。参见 `docs/agents/issue-tracker.md`。

### Triage labels

标准五角色分流标签（`needs-triage`、`needs-info`、`ready-for-agent`、`ready-for-human`、`wontfix`）。参见 `docs/agents/triage-labels.md`。

### Domain docs

单上下文架构（根目录包含 `CONTEXT.md` 与 `docs/adr/`）。参见 `docs/agents/domain.md`。

### 验证闸门

改动引擎后必须跑的两道闸门（CI 同样跑这两条）：

```bash
PYTHONPATH=src pytest -q

PYTHONPATH=src python3 -m proj7k.batch \
  --manifest docs/research/structured_index.json \
  --corpus tests/fixtures/benchmark_corpus.json.gz \
  -j 4 \
  --guard
```

两道闸门都断言引擎的实际产物——**星级**与各池自有技能的星级，而非它背后的原始特征。引擎是 `src/proj7k/engine/`（规格 v0.2，ADR-0017/0018）；`batch` 读它的 `evaluate_osu`。

- 护栏查 15 级段位阶梯的单调性（Kendall τ / Spearman ρ / 倒挂数）：总星级按池 + 阶梯级门槛；八个池各自的「自有技能」阶梯（`own_skill`）按池棘轮（`guard.OWN_SKILL_RATCHET`），上限是当前实测值、记为已知缺陷而非验收。另查 0th、5th、10th、Stellium 四档的八池中位数区间（`dan.CANONICAL_DAN_SR_BANDS`）。
- 测试套件在此之上钉死 120 首的全部星级指纹（`EXPECTED_STAR_RATING_CHECKSUM`），并钉死金标准（`tests/fixtures/engine_golden_v02.json`）、段位表与 19 张外部留出谱（`tests/engine/test_external_holdout.py`，只做检验、不参与拟合）。
- 原始密度特征（`avg_nps`、`peak_4m_nps`）降级为诊断指标，需要时用 `--guard-metric` 显式请求。
- 当前基线：测试 2 失败，均为旧引擎的预先存在项（`tests/test_tech_alignment_diagnosis.py` 的两个用例，随旧引擎删除而消失）；失败数多于这 2 个，或失败的不是它们，才是回归。

旧引擎（`radar`、`strain`、`difficulty`、`rating` 及其上的 profiler / downscaler 链）原地保留，段位表用 `dan.LEGACY_*`。

改标定是「两步提交」：先改，确认新阶梯正是想要的，再重基线化。改的东西决定重基线化哪些：

- 引擎常数（`engine/params.py`）：星级指纹、四档区间、棘轮上限、`dan_table.json`。
- 星标尺：改 `engine/anchors.json`，重拟合后更新 `engine/scale.py` 里的 `STAR_A`/`STAR_B`，再同上。

120 张标杆谱面的原始 `.osu` 内容冻结在 `tests/fixtures/benchmark_corpus.json.gz`，端到端验证与 `tests/conftest.py` 的阶梯诊断夹具都不依赖本机 osu! 安装。只有在标杆清单（`docs/research/structured_index.json`）本身变更时才需要重新冻结：

```bash
PYTHONPATH=src python3 tools/export_benchmark_corpus.py \
  --manifest docs/research/structured_index.json \
  --library-dir "$HOME/Library/Application Support/osu/files" \
  --output tests/fixtures/benchmark_corpus.json.gz
```
