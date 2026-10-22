# Contributing to proj7k

Thanks for wanting to help. proj7k is a small, one-maintainer project, so a little coordination up front saves everyone
time. Issues and pull requests are welcome in **English or Chinese** (简体 / 繁體 都可以).

[中文简介见文末](#中文简介)

## Before you start

- **Bugs and chart feedback**: open an issue with the matching template. For "this chart's stars look wrong", the
  *Star rating feedback* template asks for exactly what we need (the chart link, what you expected, and why).
- **Features and larger changes**: open an issue first and wait for a reply before writing code. The difficulty engine
  in particular is calibrated against a fixed benchmark ladder, so changes to it need agreement on the goal first.
- **Small fixes** (typos, docs, obvious bugs): just send a pull request.
- Look for issues labelled `good first issue` or `help wanted` if you want somewhere to start.

## Development setup

You need **Python 3.14** (3.13 and older fail parts of the test suite) and, for the lazer bridge and player pages,
**Node.js**. The desktop window (`desktop/tauri`) also needs Rust, but you only need it if you change the window itself.

```bash
git clone https://github.com/<you>/proj7k.git
cd proj7k
python3.14 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt pytest
```

No osu! install is needed to run the tests: the benchmark charts are frozen in `tests/fixtures/`.

## The two checks every change must pass

CI runs both on every pull request; run them locally before pushing:

```bash
PYTHONPATH=src pytest -q

PYTHONPATH=src python3 -m proj7k.batch \
  --manifest docs/research/structured_index.json \
  --corpus tests/fixtures/benchmark_corpus.json.gz \
  -j 4 \
  --guard
```

On Windows use `set PYTHONPATH=src` first. Main is always green, so any failure is a regression from your change.

- **Never skip, delete or loosen a test** to get green. If a test is wrong, say so in the PR and explain why.
- The suite pins a checksum of all 120 benchmark star ratings (`EXPECTED_STAR_RATING_CHECKSUM`). Any change that
  moves a star rating, even slightly, fails it on purpose. If that change is intended, it is a **calibration change**:
  see below.

## Changing the difficulty engine

The engine lives in `src/proj7k/engine/` (spec v0.2, ADR-0017/0018). A calibration change is a two-step commit:
first make the change and show the new ladder is the one you want (attach the guard output to the PR), then re-baseline.
What to re-baseline depends on what you changed:

- Engine constants (`engine/params.py`): the star checksum, the four tier bands, the ratchet ceilings and
  `dan_table.json`.
- The star scale (`engine/anchors.json`): refit, update `STAR_A`/`STAR_B` in `engine/scale.py`, then the same as above.

The 19 external holdout charts (`tests/engine/test_external_holdout.py`) are for checking only. Do not tune constants
against them.

## Interface text: always in two languages

The dashboard and desktop app are bilingual (ADR-0035). Pages are written in Chinese and translated at runtime:

- **JavaScript / HTML**: write the Chinese text, wrap script strings in `tr("中文 {x}", { x })`, and add an English
  entry for every new string to `src/proj7k/dashboard/static/i18n.js`. `tests/test_dashboard_i18n.py` fails on any
  Chinese text that has no English entry.
- **Python messages a player reads**: `proj7k.i18n.tr("缺少音频", "Missing audio")`.

If you do not read Chinese, write English in your PR and say so; the maintainer will add the Chinese.

## Documentation

- The README exists in English (`README.md`), Simplified Chinese (`README.zh-CN.md`) and Traditional Chinese
  (`README.zh-TW.md`). If you change usage docs, update the one you can and mention it in the PR; the others will be
  brought in sync before merge.
- Design decisions are recorded as ADRs in `docs/adr/`. A change that goes against an existing ADR should say so
  explicitly, and a significant new decision gets a new ADR.
- Domain terms are defined in `CONTEXT.md`. Use them in code, tests and issues.
- `CLAUDE.md` holds the same rules in Chinese for AI coding agents.

## Pull requests

- Keep each PR to one topic. Branch from `main`; the maintainer squash- or merge-commits it.
- Fill in the PR template: what changed, why, and how you checked it.
- New behaviour comes with tests.
- Match the style of the surrounding code (naming, comment density, docstrings). There is no formatter enforced in CI;
  keep diffs free of unrelated reformatting.
- For anything a player sees, attach a screenshot.
- CI on pull requests from forks waits for the maintainer to approve the run. That is normal.

## Content we cannot accept

- osu! code, skins, sounds or other assets, and audio files.
- New beatmaps in the repository unless they are needed for a test and their mapper allows it (see [NOTICE](NOTICE.md)).
- Secrets, tokens or anything from your own osu! account or lazer database.

## License

By contributing you agree that your contribution is licensed under the project's [MIT License](LICENSE).

## Code of conduct

Everyone taking part is expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md). Security problems go through
the private channel in [SECURITY.md](SECURITY.md), not public issues.

---

## 中文简介

欢迎用中文提 Issue 和 PR。要点：

- 新功能或大改动先开 Issue 讨论；小修（错字、文档、明显 bug）直接提 PR。觉得某张谱星级不对，用「Star rating feedback」模板。
- 环境：Python 3.14（3.13 跑不过部分测试），玩家页面和 lazer bridge 需要 Node.js。`pip install -r requirements.txt pytest`，不需要本机装 osu!。
- 提交前跑上面两道闸门（`pytest` 和 `batch --guard`），CI 也跑这两条。不许为了变绿跳过或放宽测试。
- 动了引擎星级会让 120 首星级指纹测试失败，这是有意的：标定改动按「两步提交」来，见上文或 `CLAUDE.md`。
- 界面文字双语：页面写中文，脚本里用 `tr()`，并在 `static/i18n.js` 里补英文；Python 里玩家看到的消息用 `proj7k.i18n.tr(中文, English)`。
- 改用法文档时，README 有英文、简体、繁体三份，尽量一起改。
- 贡献即同意以 MIT 许可证发布。
