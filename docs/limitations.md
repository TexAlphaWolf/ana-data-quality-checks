# Limitations

What dqcheck does not do, and where its numbers can mislead.

- **The data must fit in memory.** Each dataset is loaded into an in-memory DuckDB table before the rules run. 10 million
  orders and a million customers took about 2.1 GiB at the peak. DuckDB can spill some operations to disk, but that is
  not configured or measured here.
- **Generated data, not yours.** The benchmark's timings describe these rules on this synthetic shop: eight columns, mostly
  integers, short strings. Wide tables, long text or high-cardinality keys cost more, and a `unique` over several text
  columns is not the same work as one over an integer.
- **One machine.** The published timings come from one computer running Docker (on Windows, inside a virtual machine), with
  the cores and DuckDB version in the report. A different machine, thread count or DuckDB release changes them.
- **Rules are checked together only by tests.** A rule's time is measured alone on loaded tables; there is no measurement of
  concurrency, of a cold file cache, or of reading very many small files.
- **It looks at one snapshot.** There is no history: it cannot tell you that the null rate doubled since last week, only
  that it is above a limit you wrote. `suggest` writes limits from today's data, and that data may already be wrong.
- **Outlier rules are blunt.** `iqr` and `zscore` look at the whole column, not per group or per time period, and a
  skewed column has many legitimate "outliers". The generator's clean amounts were made to stay inside the fences so the
  rule can be tested exactly; real data will not.
- **CSV files are guessed.** DuckDB infers types from the whole file; a column with one bad value becomes text, and a
  numeric `range` on it is refused. Use `all_varchar` and `regex` for such files. Quoted and unquoted empty fields are both null.
  Parquet has real types and none of this.
- **Whole-row context is limited.** A rule looks at one dataset, or at one foreign key between two. Anything else (a
  total that must match another table, a rule per customer) is an `expression`, which is SQL over one table.
- **Rules files are trusted.** An `expression` is SQL run as written, and DuckDB can read files and, depending on the
  extensions available, reach the network. Do not run a rules file you did not read. The Docker image runs with no network
  and a read-only root (see [`SECURITY.md`](../SECURITY.md)).
- **Regular expressions are RE2.** The `regex` rule uses DuckDB's syntax, which has no backreferences or lookahead.
- **Row numbers are positions in the file.** For CSV with quoted line breaks, a record counts once.
- **DuckDB 1.5 or later.** It is tested on the latest release at the time of writing (see the report), and the `pyproject`
  allows newer ones: a major release can change a plan, a type name or a message.
