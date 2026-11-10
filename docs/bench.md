# Benchmark

Run on Linux x86_64, 12 CPUs, Python 3.14.10, DuckDB 2.0.0 with 12 threads; seed 1, 1% of the rows damaged; each timing is the median of 5 runs; 87.4 s; command `bench --sizes 100000,1000000,10000000 --repeat 5 --workdir /out --out /out`.

## 1. Scaling

The whole rule set (16 rules over two datasets) on the loaded data, and the time to load the files.

| Orders | Customers | Parquet | CSV | Load Parquet (ms) | Load CSV (ms) | All rules (ms) | Rows per second | Counts match the manifest |
|---|---|---|---|---|---|---|---|---|
| 100,000 | 10,000 | 2.4 MiB | 5.5 MiB | 83.5 | 241.5 | 38.1 | 2,888,636 | yes |
| 1,000,000 | 100,000 | 25.0 MiB | 57.2 MiB | 129.5 | 1,420.9 | 109.0 | 10,088,319 | yes |
| 10,000,000 | 1,000,000 | 253.9 MiB | 593.0 MiB | 580.3 | 12,361.3 | 825.9 | 13,318,365 | yes |

## 2. Each rule at 10,000,000 orders

| Rule | Dataset | Check | Failing rows | Median (ms) |
|---|---|---|---|---|
| C01 | customers | unique(customer_id) | 0 | 16.5 |
| C02 | customers | not_null(email) | 0 | 1.09 |
| C03 | customers | regex(email) | 10,000 | 22.6 |
| C04 | customers | allowed_values(country) | 10,000 | 6.50 |
| C05 | customers | range(signup_date) | 0 | 1.49 |
| O01 | orders | unique(order_id) | 200,000 | 171.7 |
| O02 | orders | not_null(customer_id) | 100,000 | 3.80 |
| O03 | orders | foreign_key(customer_id) | 100,000 | 242.0 |
| O04 | orders | not_null(amount_cents) | 100,000 | 5.73 |
| O05 | orders | range(amount_cents) | 100,000 | 6.18 |
| O06 | orders | allowed_values(status) | 100,000 | 33.6 |
| O07 | orders | expression | 100,000 | 9.91 |
| O08 | orders | outliers(amount_cents) | 100,000 | 309.5 |
| O09 | orders | range(ordered_at) | 100,000 | 4.54 |
| O10 | orders | freshness(ordered_at) |  | 3.54 |
| O11 | orders | row_count |  | 0.02 |
