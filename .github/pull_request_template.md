## What and why

<!-- What does this change, and what problem does it solve? Link the issue: "Closes #123". -->

## How I checked it

<!-- Commands you ran and what you saw. Screenshots for anything a player sees. -->

- [ ] `PYTHONPATH=src pytest -q` passes
- [ ] `PYTHONPATH=src python3 -m proj7k.batch --manifest docs/research/structured_index.json --corpus tests/fixtures/benchmark_corpus.json.gz -j 4 --guard` passes
- [ ] New interface text has an English entry in `static/i18n.js` (or uses `i18n.tr(zh, en)` in Python)
- [ ] Docs / READMEs updated if usage changed

## Star ratings

<!-- Does this change any chart's stars? If yes, this is a calibration change: paste the guard output before and
after, and say whether the checksum was re-baselined on purpose. Otherwise write "No change". -->
