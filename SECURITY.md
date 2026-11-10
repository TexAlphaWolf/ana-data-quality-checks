# Security

## Model

dqcheck is a command line program that **reads the files named in a rules file, runs the rules on them in an in-memory
DuckDB database, and writes a report**. It starts no service and opens no network connection of its own.

- **What it writes:** `run --out` writes `report.json` and `report.md`; `generate`, `suggest` and `bench` write the files
  they say they write. `bench` writes generated data to a temporary folder and removes it.
- **The SQL it runs** is built from the rules file. Column names are checked against the loaded table and quoted; bounds,
  allowed values, patterns and thresholds are query parameters, never part of the SQL text (`tests/test_semantics.py` tries
  quotes and `DROP TABLE` in values and a column name that carries SQL).
- **The rules file is trusted code.** The one exception to the above is the `expression` rule: its `sql` is a condition
  written by the author of the file and runs as written in DuckDB, which can read local files and, with extensions, use the
  network. Only run rules files you have read. A data file is not trusted the same way: it is read as data (CSV or
  Parquet), and its content never becomes SQL.
- **The Docker image** runs as an unprivileged user and works with a read-only root filesystem (plus `--tmpfs /tmp` for
  matplotlib's cache), no capabilities and no network (`--network none`); see `tools/docker_e2e.sh`.
- **No real data** is in the repository: everything the tests and the benchmark use is generated from a seed.

## Reporting

If you find a vulnerability, please open a private security advisory on the GitHub repository (Security tab) rather than a
public issue.
