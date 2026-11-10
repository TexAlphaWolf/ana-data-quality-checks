# Changelog

## 1.0.0

First release.

- Ten rule types in a YAML file (`not_null`, `unique`, `range`, `allowed_values`, `regex`, `foreign_key`, `outliers`,
  `expression`, `row_count`, `freshness`), run as SQL on DuckDB over CSV and Parquet; every offending row is reported with
  its position in the file.
- Severity, tolerance by count or by share, and exit codes for a pipeline.
- `profile` and `suggest` (a first rules file written from the data).
- A generator that injects eleven kinds of fault in exact numbers, with a manifest that says which rule must report which
  rows; every rule is tested against the manifest and against a second implementation in plain Python.
- A benchmark up to 10 million orders that checks every count against the manifest.
- Docker image (no network, read-only, no capabilities); CI on Python 3.11 to 3.14, Linux, Windows and macOS.
