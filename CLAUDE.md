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

两道闸门都断言引擎的实际产物——**星级**，而非它背后的原始特征：护栏查 15 级段位阶梯的单调性（Kendall τ / Spearman ρ / 倒挂数）与 0th、5th、10th、Stellium 四档锚点中位数区间；测试套件在此之上再钉死 120 首的全部星级指纹（`EXPECTED_STAR_RATING_CHECKSUM`），连不改变阶梯形状与标尺的小幅公式变动也会被拦下。原始密度特征（`avg_nps`、`peak_4m_nps`）降级为诊断指标，需要时用 `--guard-metric` 显式请求。

因此改标定常数是「两步提交」：先改常数，确认新阶梯正是想要的，再把星级指纹与验收线重新基线化。

120 张标杆谱面的原始 `.osu` 内容冻结在 `tests/fixtures/benchmark_corpus.json.gz`，端到端验证与 `tests/conftest.py` 的阶梯诊断夹具都不依赖本机 osu! 安装。只有在标杆清单（`docs/research/structured_index.json`）本身变更时才需要重新冻结：

```bash
PYTHONPATH=src python3 tools/export_benchmark_corpus.py \
  --manifest docs/research/structured_index.json \
  --library-dir "$HOME/Library/Application Support/osu/files" \
  --output tests/fixtures/benchmark_corpus.json.gz
```
