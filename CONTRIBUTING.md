# Contributing

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy && pytest --cov
```

No database or service is needed. `DQCHECK_TEST_ROWS` and `DQCHECK_TEST_SEED` run the same tests on other generated data
(CI also uses 100,000 orders with seed 11).

Rules that keep the project honest:

- **A new rule type needs three things:** its keys in `SPECS` and a line in `DESCRIPTIONS` (`rules.py`), its query in
  `Session.violations` (or its own runner), and its answer in `tests/reference.py`, computed in plain Python from the
  definition, not from the SQL. Add a fault to `gen.py` if the generated data should exercise it, and a case with a
  null, a boundary value and an empty input to `tests/test_semantics.py`.
- **Values go in as parameters.** Never paste a value from the rules file into SQL; column names go through
  `Session.column` and `quote`.
- **Numbers in the README come from `docs/bench.json`**, and `tests/test_docs.py` checks them. After a new measurement
  (`tools/run_full.ps1`), copy `bench.json`, `bench.md` and `summary.png` into `docs/` and update the text if a number moved.
