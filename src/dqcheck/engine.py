"""Loads the datasets into DuckDB and runs the rules as SQL. Every rule becomes a query that selects the offending rows."""

from __future__ import annotations

import difflib
import re
import time
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from dqcheck.rules import ROW_LEVEL, ConfigError, DatasetSpec, Rule, RuleSet

NUMERIC = {
    "TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT",
    "UHUGEINT", "FLOAT", "DOUBLE",
}  # fmt: skip


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def jsonable(value: Any) -> Any:
    """A value from a result row, as something json can write (dates and decimals become text)."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else str(value)
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


@dataclass
class Table:
    name: str
    rows: int
    columns: dict[str, str]  # name -> DuckDB type
    load_ms: float


@dataclass
class Outcome:
    """What one rule found. status: pass, fail (an error-severity rule failed), warn, or error (it could not run)."""

    rule: Rule
    status: str
    failing_rows: int | None
    total_rows: int
    observed: str | None
    message: str
    time_ms: float
    samples: list[dict[str, Any]]

    @property
    def rate(self) -> float | None:
        if self.failing_rows is None:
            return None
        return self.failing_rows / self.total_rows if self.total_rows else 0.0


class Session:
    """The datasets of a rules file, loaded into an in-memory DuckDB database."""

    @classmethod
    def from_files(cls, paths: list[Path], threads: int | None = None) -> Session:
        """A session over data files alone, with no rules (for profile and suggest). Names come from the file names."""
        specs: dict[str, DatasetSpec] = {}
        for path in paths:
            name = re.sub(r"\W", "_", path.stem)
            name = f"_{name}" if not name or name[0].isdigit() else name
            fmt = {".parquet": "parquet", ".csv": "csv"}.get(path.suffix.lower())
            if fmt is None:
                raise ConfigError(f"{path.name}: use a .csv or .parquet file")
            if name in specs:
                raise ConfigError(f"two files are called {path.stem}: rename one")
            specs[name] = DatasetSpec(name, path, fmt)
        return cls(RuleSet(specs, []), threads)

    def __init__(self, ruleset: RuleSet, threads: int | None = None) -> None:
        self.ruleset = ruleset
        self.con = duckdb.connect(":memory:")
        if threads:
            self.con.execute(f"SET threads = {int(threads)}")
        self.tables: dict[str, Table] = {}
        for spec in ruleset.datasets.values():
            self.tables[spec.name] = self._load(spec)

    def close(self) -> None:
        self.con.close()

    def __enter__(self) -> Session:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _load(self, spec: DatasetSpec) -> Table:
        if not spec.path.is_file():
            raise ConfigError(f"dataset {spec.name}: {spec.path} does not exist")
        csv_options = "header = true, " + ("all_varchar = true" if spec.all_varchar else "sample_size = -1")
        reader = "read_parquet(?)" if spec.format == "parquet" else f"read_csv(?, {csv_options})"
        start = time.perf_counter()
        try:
            self.con.execute(f"CREATE TABLE {quote(spec.name)} AS SELECT * FROM {reader}", [str(spec.path)])
        except duckdb.Error as exc:
            raise ConfigError(f"dataset {spec.name}: cannot read {spec.path.name}: {exc}") from exc
        load_ms = (time.perf_counter() - start) * 1000
        described = self.con.execute(f"DESCRIBE {quote(spec.name)}").fetchall()
        rows = self.con.execute(f"SELECT count(*) FROM {quote(spec.name)}").fetchone()
        assert rows is not None
        return Table(spec.name, int(rows[0]), {r[0]: r[1] for r in described}, load_ms)

    def column(self, dataset: str, name: str) -> str:
        """The column's real name (DuckDB ignores case), or a ConfigError that suggests the nearest one."""
        columns = self.tables[dataset].columns
        by_lower = {c.lower(): c for c in columns}
        if name.lower() in by_lower:
            return by_lower[name.lower()]
        near = difflib.get_close_matches(name, list(columns), n=1)
        hint = f" (did you mean {near[0]}?)" if near else f" (columns: {', '.join(columns)})"
        raise ConfigError(f"dataset {dataset} has no column {name!r}{hint}")

    def ctype(self, dataset: str, column: str) -> str:
        return self.tables[dataset].columns[column]

    # ---- compiling a rule into the query that selects its offending rows ----

    def _bound(self, dataset: str, column: str, value: Any) -> tuple[str, Any]:
        ctype = self.ctype(dataset, column)
        if ctype in NUMERIC or ctype.startswith("DECIMAL"):
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise ConfigError(f"column {column} is {ctype}: min and max must be numbers, got {value!r}")
            return "CAST(? AS DOUBLE)", float(value)
        if ctype == "VARCHAR" and not isinstance(value, str):
            raise ConfigError(
                f"column {column} is text (VARCHAR): a numeric or date bound would compare as text. "
                "Either the column has a value that is not a number or a date, or the file needs all_varchar and a regex"
            )
        return f"CAST(? AS {ctype})", value.isoformat() if isinstance(value, date) else str(value)

    def violations(self, rule: Rule) -> tuple[str, list[Any]]:
        """SQL that returns one row, _rid, for each offending row of a row-level rule, and its parameters."""
        p = rule.params
        t = quote(rule.dataset)
        col = (lambda key="column": quote(self.column(rule.dataset, p[key]))) if "column" in p else None
        if rule.type == "not_null":
            assert col
            return f"SELECT rowid AS _rid FROM {t} WHERE {col()} IS NULL", []
        if rule.type == "unique":
            keys = [quote(self.column(rule.dataset, c)) for c in p["columns"]]
            present = " AND ".join(f"{k} IS NOT NULL" for k in keys)
            return (
                f"SELECT _rid FROM (SELECT rowid AS _rid, count(*) OVER (PARTITION BY {', '.join(keys)}) AS _n "
                f"FROM {t} WHERE {present}) WHERE _n > 1",
                [],
            )
        if rule.type == "range":
            assert col
            c = col()
            name = self.column(rule.dataset, p["column"])
            parts: list[str] = []
            params: list[Any] = []
            if "min" in p:
                sql, value = self._bound(rule.dataset, name, p["min"])
                parts.append(f"{c} < {sql}")
                params.append(value)
            if "max" in p:
                sql, value = self._bound(rule.dataset, name, p["max"])
                parts.append(f"{c} > {sql}")
                params.append(value)
            return f"SELECT rowid AS _rid FROM {t} WHERE {c} IS NOT NULL AND ({' OR '.join(parts)})", params
        if rule.type == "allowed_values":
            assert col
            c = col()
            marks = ", ".join("?" for _ in p["values"])
            return (
                f"SELECT rowid AS _rid FROM {t} WHERE {c} IS NOT NULL AND CAST({c} AS VARCHAR) NOT IN ({marks})",
                list(p["values"]),
            )
        if rule.type == "regex":
            assert col
            c = col()
            return (
                f"SELECT rowid AS _rid FROM {t} WHERE {c} IS NOT NULL "
                f"AND NOT regexp_full_match(CAST({c} AS VARCHAR), ?)",
                [p["pattern"]],
            )
        if rule.type == "foreign_key":
            assert col
            c = col()
            ref = p["ref_dataset"]
            rc = quote(self.column(ref, p["ref_column"]))
            return (
                f"SELECT t.rowid AS _rid FROM {t} AS t WHERE t.{c} IS NOT NULL "
                f"AND NOT EXISTS (SELECT 1 FROM {quote(ref)} AS r WHERE r.{rc} = t.{c})",
                [],
            )
        if rule.type == "outliers":
            assert col
            c = col()
            if p["method"] == "iqr":
                return (
                    f"WITH q AS (SELECT quantile_cont({c}, 0.25) AS q1, quantile_cont({c}, 0.75) AS q3 FROM {t}), "
                    f"f AS (SELECT q1 - ? * (q3 - q1) AS lo, q3 + ? * (q3 - q1) AS hi FROM q) "
                    f"SELECT t.rowid AS _rid FROM {t} AS t, f WHERE t.{c} IS NOT NULL AND (t.{c} < f.lo OR t.{c} > f.hi)",
                    [p["threshold"], p["threshold"]],
                )
            return (
                f"WITH s AS (SELECT avg({c}) AS m, stddev_samp({c}) AS sd FROM {t}) "
                f"SELECT t.rowid AS _rid FROM {t} AS t, s WHERE t.{c} IS NOT NULL AND s.sd > 0 "
                f"AND abs(t.{c} - s.m) / s.sd > ?",
                [p["threshold"]],
            )
        if rule.type == "expression":
            return f"SELECT rowid AS _rid FROM {t} WHERE NOT ({p['sql']})", []
        raise AssertionError(rule.type)

    # ---- running ----

    def run(self, rule: Rule, sample: int = 5, as_of: date | None = None) -> Outcome:
        total = self.tables[rule.dataset].rows
        start = time.perf_counter()
        try:
            if rule.type in ROW_LEVEL:
                outcome = self._run_rows(rule, total, sample)
            elif rule.type == "row_count":
                outcome = self._run_row_count(rule, total)
            else:
                outcome = self._run_freshness(rule, total, as_of)
        except ConfigError as exc:
            outcome = Outcome(rule, "error", None, total, None, str(exc), 0.0, [])
        except duckdb.Error as exc:
            outcome = Outcome(rule, "error", None, total, None, f"the query failed: {exc}", 0.0, [])
        outcome.time_ms = (time.perf_counter() - start) * 1000
        return outcome

    def _verdict(self, rule: Rule, failed: bool) -> str:
        if not failed:
            return "pass"
        return "fail" if rule.severity == "error" else "warn"

    def _run_rows(self, rule: Rule, total: int, sample: int) -> Outcome:
        sql, params = self.violations(rule)
        row = self.con.execute(f"SELECT count(*) FROM ({sql})", params).fetchone()
        assert row is not None
        failing = int(row[0])
        rate = failing / total if total else 0.0
        tolerated = failing <= rule.max_failures or rate <= rule.max_failure_rate  # either allowance is enough
        samples: list[dict[str, Any]] = []
        if failing and sample:
            t = quote(rule.dataset)
            cursor = self.con.execute(
                f"SELECT t.rowid + 1 AS row, t.* FROM {t} AS t JOIN "
                f"(SELECT _rid FROM ({sql}) ORDER BY _rid LIMIT {int(sample)}) AS v ON t.rowid = v._rid ORDER BY t.rowid",
                params,
            )
            names = [d[0] for d in cursor.description]
            samples = [{n: jsonable(v) for n, v in zip(names, r, strict=True)} for r in cursor.fetchall()]
        if failing == 0:
            message = "no offending rows"
        elif tolerated:
            message = f"{failing:,} offending rows, within the tolerance"
        else:
            message = f"{failing:,} of {total:,} rows ({rate:.2%})"
        return Outcome(rule, self._verdict(rule, not tolerated), failing, total, None, message, 0.0, samples)

    def _run_row_count(self, rule: Rule, total: int) -> Outcome:
        low, high = rule.params.get("min"), rule.params.get("max")
        bad = (low is not None and total < low) or (high is not None and total > high)
        bounds = " and ".join(
            part
            for part in (
                f"at least {low:,}" if low is not None else "",
                f"at most {high:,}" if high is not None else "",
            )
            if part
        )
        message = f"{total:,} rows, expected {bounds}"
        return Outcome(rule, self._verdict(rule, bad), None, total, f"{total:,} rows", message, 0.0, [])

    def _run_freshness(self, rule: Rule, total: int, as_of: date | None) -> Outcome:
        column = self.column(rule.dataset, rule.params["column"])
        reference = as_of or date.today()
        row = self.con.execute(
            f"SELECT CAST(max({quote(column)}) AS DATE) FROM {quote(rule.dataset)}",
        ).fetchone()
        assert row is not None
        newest = row[0]
        if newest is None:
            return Outcome(rule, self._verdict(rule, True), None, total, None, f"{column} has no values", 0.0, [])
        age = (reference - newest).days
        limit = rule.params["max_age_days"]
        stale = age > limit
        if age < 0:
            message = f"newest {column} is {newest.isoformat()}, after {reference.isoformat()}"
        else:
            message = (
                f"newest {column} is {newest.isoformat()}, {age} days before {reference.isoformat()} (limit {limit:g})"
            )
        return Outcome(rule, self._verdict(rule, stale), None, total, newest.isoformat(), message, 0.0, [])


def run_all(session: Session, sample: int = 5, as_of: date | None = None) -> list[Outcome]:
    effective = as_of or session.ruleset.as_of
    return [session.run(rule, sample, effective) for rule in session.ruleset.rules]


def exit_code(outcomes: list[Outcome], strict: bool = False) -> int:
    bad = {"fail", "error", "warn"} if strict else {"fail", "error"}
    return 1 if any(o.status in bad for o in outcomes) else 0
