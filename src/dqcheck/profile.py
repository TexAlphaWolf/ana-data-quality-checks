"""A look at the columns of a dataset, and a first set of rules written from it."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dqcheck.engine import NUMERIC, Session, quote

TEMPORAL = ("DATE", "TIMESTAMP")
DISCRETE_MAX = 12


def _orderable(ctype: str) -> bool:
    return ctype in NUMERIC or ctype.startswith(("DECIMAL", *TEMPORAL))


def profile_table(session: Session, name: str) -> list[dict[str, Any]]:
    table = session.tables[name]
    out: list[dict[str, Any]] = []
    for column, ctype in table.columns.items():
        c = quote(column)
        mean = f", avg({c})" if ctype in NUMERIC or ctype.startswith("DECIMAL") else ", NULL"
        low_high = f", min({c}), max({c})" if _orderable(ctype) or ctype == "VARCHAR" else ", NULL, NULL"
        row = session.con.execute(
            f"SELECT count(*) - count({c}), count(DISTINCT {c}){low_high}{mean} FROM {quote(name)}"
        ).fetchone()
        assert row is not None
        nulls, distinct, low, high, avg = row
        out.append(
            {
                "column": column,
                "type": ctype,
                "rows": table.rows,
                "nulls": int(nulls),
                "null_rate": nulls / table.rows if table.rows else 0.0,
                "distinct": int(distinct),
                "min": None if low is None else str(low),
                "max": None if high is None else str(high),
                "mean": None if avg is None else float(avg),
            }
        )
    return out


def render_profile(name: str, rows: int, columns: list[dict[str, Any]]) -> str:
    lines = [
        f"### {name} ({rows:,} rows)",
        "",
        "| Column | Type | Nulls | Distinct | Min | Max | Mean |",
        "|---|---|---|---|---|---|---|",
    ]
    for c in columns:
        mean = "" if c["mean"] is None else f"{c['mean']:,.2f}"
        lines.append(
            f"| {c['column']} | {c['type']} | {c['nulls']:,} ({c['null_rate']:.1%}) | {c['distinct']:,} | "
            f"{c['min'] if c['min'] is not None else ''} | {c['max'] if c['max'] is not None else ''} | {mean} |"
        )
    return "\n".join(lines) + "\n"


def suggest_rules(session: Session, name: str) -> list[dict[str, Any]]:
    """Rules that describe the data as it is now: a starting point to edit, not a statement of what is right."""
    rules: list[dict[str, Any]] = []
    rows = session.tables[name].rows
    if rows == 0:
        return rules
    rules.append({"dataset": name, "type": "row_count", "min": 1})
    for c in profile_table(session, name):
        column, ctype = c["column"], c["type"]
        if c["nulls"] == 0:
            rules.append({"dataset": name, "type": "not_null", "column": column})
        if c["distinct"] == rows and ctype not in ("FLOAT", "DOUBLE", "BOOLEAN"):
            rules.append({"dataset": name, "type": "unique", "columns": [column]})
        if _orderable(ctype) and c["min"] is not None and c["distinct"] > 1:
            numeric = ctype in NUMERIC or ctype.startswith("DECIMAL")
            low = float(c["min"]) if numeric else c["min"]
            high = float(c["max"]) if numeric else c["max"]
            if numeric and ctype not in ("FLOAT", "DOUBLE") and not ctype.startswith("DECIMAL"):
                low, high = int(low), int(high)
            rules.append({"dataset": name, "type": "range", "column": column, "min": low, "max": high})
        if ctype == "VARCHAR" and 1 < c["distinct"] <= DISCRETE_MAX and rows >= 5 * c["distinct"]:
            values = session.con.execute(
                f"SELECT DISTINCT {quote(column)} FROM {quote(name)} WHERE {quote(column)} IS NOT NULL ORDER BY 1"
            ).fetchall()
            rules.append(
                {"dataset": name, "type": "allowed_values", "column": column, "values": [v[0] for v in values]}
            )
    return rules


def suggestion_document(session: Session, out_dir: Path) -> dict[str, Any]:
    rules: list[dict[str, Any]] = []
    for name in session.tables:
        rules.extend(suggest_rules(session, name))
    for index, rule in enumerate(rules, start=1):
        rule["id"] = f"S{index:02d}"
        rules[index - 1] = {"id": rule.pop("id"), **rule}
    datasets = {
        name: os.path.relpath(session.ruleset.datasets[name].path.resolve(), out_dir.resolve()).replace("\\", "/")
        for name in session.tables
    }
    return {"datasets": datasets, "rules": rules}
