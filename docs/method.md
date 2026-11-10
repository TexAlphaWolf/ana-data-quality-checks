# Method

## What a rule says

A rule selects the rows that break it. `failing_rows` is how many there are, `rate` is that over the dataset's rows.

| Type | Offending rows | Whole-dataset result |
|---|---|---|
| `not_null` | the column is null | |
| `unique` | the key (one or more columns) appears in another row; every row of a duplicate group is reported; a null in the key skips the row | |
| `range` | the value is below `min` or above `max` (both inclusive bounds); nulls are skipped | |
| `allowed_values` | the value, as text, is not in `values`; nulls are skipped | |
| `regex` | the value, as text, does not match the whole `pattern` (RE2 syntax); nulls are skipped | |
| `foreign_key` | the value has no match in `ref_dataset.ref_column`; nulls are skipped | |
| `outliers` | `iqr`: below Q1 - t·IQR or above Q3 + t·IQR (t = `threshold`, default 1.5; quartiles interpolated linearly). `zscore`: more than `threshold` (default 3) sample standard deviations from the mean | |
| `expression` | the SQL condition, which describes a valid row, is false; null counts as valid (as in a CHECK constraint) | |
| `row_count` | | fewer than `min` or more than `max` rows |
| `freshness` | | the newest value is more than `max_age_days` days before `as_of` |

`dqcheck types` prints the same list with the keys of each type.

## Result, severity and exit code

A rule **passes** when its offending rows are within the tolerance: at most `max_failures` rows, or at most
`max_failure_rate` of the dataset, whichever allows more (both default to 0, so nothing is tolerated). The offending rows
are reported even when tolerated. A rule that does not pass is a **fail** (`severity: error`, the default) or a **warn**
(`severity: warn`). A rule that cannot run (a column that does not exist, a SQL error, a bound that does not fit the column)
is an **error**, and the other rules still run. The exit code is 1 if any rule failed or errored (with `--strict`, also
on a warning), 2 for a mistake in the rules file or the data files, 0 otherwise.

## Rows and files

- Parquet files keep their types. CSV files are read with the types DuckDB finds in the whole file (`sample_size = -1`);
  one value that is not a number makes the whole column text, and a numeric or date bound on a text column is refused
  instead of compared as text. An empty CSV field is null, quoted or not. A dataset can set `all_varchar: true` to
  read every CSV column as text, as written, and then check formats with `regex` and `expression`.
- A row is identified by its position in the file: `row` 1 is the first data row, after the header. Loading keeps the order
  of the file, and the first offending rows are always the lowest row numbers.
- A freshness check measures against `as_of` (in the rules file, or `--as-of`, or today).

## The generated data and its faults

`dqcheck generate` writes customers (a tenth as many as orders) and orders. A share of rows (`--error-rate`, 1% by default,
at most 5%) is damaged for each kind of fault; every kind takes its own rows, drawn at random, so no row has two faults, and
`manifest.json` records for every rule how many rows it must report (and which, for up to 10,000 rows).

| Fault | Rows | What is changed | Rule |
|---|---|---|---|
| `null_customer` | orders | `customer_id` becomes null | O02 |
| `null_amount` | orders | `amount_cents` becomes null | O04 |
| `duplicate_order_id` | orders | a row takes the `order_id` of another, so both are reported (2 rows per fault) | O01 |
| `orphan_customer` | orders | `customer_id` points to a customer that does not exist | O03 |
| `negative_amount` | orders | a small negative unit price and amount (they stay consistent) | O05 |
| `bad_status` | orders | a status such as `PAID` or `shiped` | O06 |
| `mismatch_amount` | orders | `amount_cents` is 1 to 499 more than quantity times unit price | O07 |
| `outlier_amount` | orders | amount and unit price multiplied by 1,000 (they stay consistent) | O08 |
| `future_date` | orders | `ordered_at` after the `as_of` date | O09 |
| `bad_email` | customers | `not-an-email`, `a@b` and the like | C03 |
| `bad_country` | customers | `XX`, `usa`, `Italy` | C04 |

The damage is chosen so that each fault is seen by one rule only: the negative and outlier faults keep amount equal to
quantity times unit price, and the clean amounts stay inside the IQR fences (a property the tests check for several seeds),
so the outlier rule has no false alarms. C01, C02, C05, O10 and O11 find nothing in the data and must report 0.

## How it is verified

1. Every rule is run on the generated data, as Parquet and as CSV, and must report exactly the rows in the manifest.
2. Every rule is redone in plain Python over the rows (`tests/reference.py`) and must flag the same rows.
3. The tests run on 4,000 orders with seed 7, and in CI also on 100,000 orders with seed 11.
4. The benchmark compares every count with the manifest at every size (`verified` in `bench.json`), up to 10,000,000
   orders; it exits with 1 if one differs.

## How the time is measured

- The datasets are loaded into DuckDB tables first (the load time is reported on its own, for Parquet and for CSV), then
  every rule runs on the tables. A rule's time is the whole of its query, counting the offending rows; no sample is fetched.
- **Median of N runs** (5 in the published run). "All rules" is the sum of the 16 rules' times in one run, and the median of
  that over the runs. Rows per second is the rows of both datasets over that time.
- DuckDB uses all the cores of the machine (the report says how many); timings from a different machine are not comparable.
- The files are generated again for each size, and removed at the end.
