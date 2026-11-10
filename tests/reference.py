"""Every rule type again, in plain Python over rows read from the file: the second opinion the SQL is compared with.

It is written from the rule's definition (see DESCRIPTIONS in dqcheck.rules), not from the SQL, and it works on lists of
dicts, so it shares no code with the engine. Each function returns the set of 1-based row numbers it flags.
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Callable
from pathlib import Path
from typing import Any

import duckdb

Rows = list[dict[str, Any]]


def read(path: Path) -> Rows:
    con = duckdb.connect()
    reader = "read_parquet(?)" if path.suffix == ".parquet" else "read_csv(?, header = true, sample_size = -1)"
    cur = con.execute(f"SELECT * FROM {reader}", [str(path)])
    names = [d[0] for d in cur.description]
    rows = [dict(zip(names, r, strict=True)) for r in cur.fetchall()]
    con.close()
    return rows


def not_null(rows: Rows, column: str) -> set[int]:
    return {i for i, r in enumerate(rows, 1) if r[column] is None}


def unique(rows: Rows, columns: list[str]) -> set[int]:
    seen: dict[tuple[Any, ...], list[int]] = {}
    for i, r in enumerate(rows, 1):
        key = tuple(r[c] for c in columns)
        if None not in key:
            seen.setdefault(key, []).append(i)
    return {i for group in seen.values() if len(group) > 1 for i in group}


def value_range(rows: Rows, column: str, low: Any = None, high: Any = None) -> set[int]:
    out = set()
    for i, r in enumerate(rows, 1):
        v = r[column]
        if v is not None and ((low is not None and v < low) or (high is not None and v > high)):
            out.add(i)
    return out


def allowed_values(rows: Rows, column: str, values: list[str]) -> set[int]:
    return {i for i, r in enumerate(rows, 1) if r[column] is not None and str(r[column]) not in values}


def regex(rows: Rows, column: str, pattern: str) -> set[int]:
    compiled = re.compile(pattern)
    return {i for i, r in enumerate(rows, 1) if r[column] is not None and not compiled.fullmatch(str(r[column]))}


def foreign_key(rows: Rows, column: str, ref_rows: Rows, ref_column: str) -> set[int]:
    known = {r[ref_column] for r in ref_rows if r[ref_column] is not None}
    return {i for i, r in enumerate(rows, 1) if r[column] is not None and r[column] not in known}


def quantile(sorted_values: list[float], q: float) -> float:
    """Linear interpolation between the closest ranks, as DuckDB's quantile_cont does."""
    position = q * (len(sorted_values) - 1)
    low = int(position)
    high = min(low + 1, len(sorted_values) - 1)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low)


def outliers_iqr(rows: Rows, column: str, factor: float) -> set[int]:
    values = sorted(r[column] for r in rows if r[column] is not None)
    q1, q3 = quantile(values, 0.25), quantile(values, 0.75)
    low, high = q1 - factor * (q3 - q1), q3 + factor * (q3 - q1)
    return {i for i, r in enumerate(rows, 1) if r[column] is not None and (r[column] < low or r[column] > high)}


def outliers_zscore(rows: Rows, column: str, threshold: float) -> set[int]:
    values = [r[column] for r in rows if r[column] is not None]
    if len(values) < 2:
        return set()
    mean, sd = statistics.fmean(values), statistics.stdev(values)
    if sd == 0:
        return set()
    return {i for i, r in enumerate(rows, 1) if r[column] is not None and abs(r[column] - mean) / sd > threshold}


def expression(rows: Rows, valid: Callable[[dict[str, Any]], bool | None]) -> set[int]:
    """valid returns False for an invalid row; None (a null in the arithmetic) counts as valid."""
    return {i for i, r in enumerate(rows, 1) if valid(r) is False}
