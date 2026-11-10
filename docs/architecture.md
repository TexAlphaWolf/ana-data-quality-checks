# Architecture

```
rules.yml ──> rules.py (validate) ──> RuleSet ──┐
                                                ├──> engine.Session ──> Outcome per rule ──> report.py ──> report.json, report.md
CSV / Parquet files ───────────────────────────┘     (DuckDB, in memory)

gen.py ──> numpy columns with injected faults ──> files + rules.yml + manifest.json ──> tests, bench.py
profile.py ──> column profile, suggested rules
```

| Module | What it does |
|---|---|
| `rules.py` | The rules file. Ten rule types, each with the keys it requires and allows; every mistake becomes a `ConfigError` with the rule's id in the message. It never touches data. |
| `engine.py` | `Session` loads each dataset into a DuckDB table (so a row has a stable position, its `rowid`) and compiles a rule into the query that selects its offending rows. Counting, sampling and tolerance are the same for every row-level rule; whole-dataset rules (`row_count`, `freshness`) have their own small runners. A rule that cannot run becomes an `error` outcome and the others continue. |
| `gen.py` | The synthetic shop: customers and orders, with eleven kinds of fault injected in exact numbers on disjoint rows, the rules file that finds them, and `manifest.json` saying which rule must report which rows. |
| `profile.py` | Column profile (nulls, distinct values, min, max, mean) and `suggest`, which writes rules from what the data looks like today. |
| `bench.py` | Generates data of growing size, times loading (Parquet and CSV) and each rule, and checks every count against the manifest. |
| `report.py` | The JSON record, and the Markdown report and figures rebuilt from it alone (`dqcheck render`). |
| `cli.py` | `run`, `profile`, `suggest`, `generate`, `bench`, `render`, `types`. Exit codes: 0 all rules pass, 1 a rule failed (or could not run), 2 a mistake in the rules file or the files. |

## How a rule becomes SQL

Every row-level rule is a query that returns the `rowid` of each offending row; the engine wraps it three ways:

```sql
SELECT count(*) FROM (<offending rows>)                                  -- how many
SELECT t.rowid + 1 AS row, t.* FROM t JOIN (<offending rows> LIMIT n) ... -- the first n, in file order
```

For example, `unique(order_id)` is a window count over the key, `foreign_key` is a `NOT EXISTS`, `outliers` computes the
quartiles in a CTE. Column names are looked up in the loaded table (so a typo gets a suggestion) and quoted; values (bounds,
allowed values, patterns, thresholds) are query parameters, never pasted into the SQL. The one deliberate exception is the
`expression` rule, whose condition is SQL written by the person who wrote the rules file.

## What the tests check

- **The manifest.** Every rule, on Parquet and on CSV, reports exactly the injected rows (`tests/test_engine.py`).
- **A second implementation.** `tests/reference.py` redoes every rule type in plain Python over the rows read from the file;
  the two must flag the same rows.
- **The corners.** `tests/test_semantics.py` uses files made by hand: where a bound is inclusive, what a null does, how
  the tolerance combines, what happens to a bad column name or a failing query.
- **The file format and the CLI.** Every mistake in a rules file has a test for its message; every command has one for its
  output and exit code, including `suggest` writing rules that pass on the data they came from.
- **The documents.** `tests/test_docs.py` compares every number in the README with `docs/bench.json`.
