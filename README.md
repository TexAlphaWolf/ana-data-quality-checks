# dqcheck

> Work in progress: the results section is written after the first measurement.

Data quality checks from a YAML file, run as SQL on DuckDB over CSV and Parquet.

```bash
dqcheck generate --rows 10000 --out demo
dqcheck run demo/rules.yml --out demo/out
```
