# Analysis of the benchmark

Source: [`bench.json`](bench.json), rendered in [`bench.md`](bench.md). One run in Docker on Linux x86_64, 12 CPUs and
12 DuckDB threads, Python 3.14.10, DuckDB 2.0.0, seed 1, 1% of the rows damaged, median of 5 runs. The rules are the 16 of
the generated `rules.yml`. Every count at every size equalled the manifest.

## 1. Time grows slower than the data

| Orders | All rules (ms) | Rows per second |
|---|---|---|
| 100,000 | 38.1 | 2,888,636 |
| 1,000,000 | 109.0 | 10,088,319 |
| 10,000,000 | 825.9 | 13,318,365 |

From 100,000 to 10,000,000 orders (100 times the data) the time grows 21.7 times. At the smallest size part of the time is
fixed cost (planning 16 queries, starting threads), so throughput rises until the data is large enough to keep 12 threads
busy. This is a statement about this machine and these rules; it is not a scaling law.

## 2. Loading is the larger cost

| Orders | Load Parquet (ms) | Load CSV (ms) | CSV / Parquet |
|---|---|---|---|
| 100,000 | 83.5 | 241.5 | 2.9x |
| 1,000,000 | 129.5 | 1,420.9 | 11.0x |
| 10,000,000 | 580.3 | 12,361.3 | 21.3x |

At 10 million orders the CSV load (12.4 s) is about 15 times the time of all the rules (0.83 s), and even the Parquet load
is 0.7 times the time of the rules' run (check time is 1.4 times the Parquet load). CSV needs parsing and type inference over the whole file
(`sample_size=-1`); Parquet carries types and is read by column. The Parquet file is also 2.3 times smaller
(253.9 against 593.0 MiB). For a repeated check, converting a CSV to Parquet once is worth more than any change to a rule.

## 3. Which rules cost

At 10,000,000 orders:

| Rule | Median (ms) | Why |
|---|---|---|
| O08 outliers | 309.5 | two quantiles over 10 million values, then a scan |
| O03 foreign_key | 242.0 | an anti-join of 10 million orders with 1 million customers |
| O01 unique(order_id) | 171.7 | a hash aggregation over 10 million keys |
| O06 allowed_values | 33.6 | text comparison |
| C03 regex | 22.6 | RE2 on 1 million e-mails |
| everything else | 1 to 17 | one pass over a column, often on metadata or a single column |

The three largest are 723 of the 839 ms of summed medians (86%). Null checks, ranges and the expression are between 4 and
10 ms. The reasons in the last column are the usual cost of those operations, not something this run isolates.

## 4. Caveats

- **12 cores.** DuckDB uses all threads it is given; on 2 cores the heavy rules will take proportionally longer. The
  thread count is in the report.
- **Docker on Windows** runs in a virtual machine, so absolute times include that layer.
- **Synthetic shop.** Eight columns, mostly integers and short strings. See [`limitations.md`](limitations.md).
- **Tables in memory.** The 10 million order run needs about 2.1 GiB of memory for the loaded tables.
- **One run per size** of 5 repetitions; the spread is not reported, and the medians of the small rules (under 2 ms) are
  close to timer and scheduling noise.
