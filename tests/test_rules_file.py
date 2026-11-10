"""The rules file: what is accepted, and the message for everything that is not."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from dqcheck import rules
from dqcheck.rules import ConfigError, load_rules, parse_rules

BASE = Path("/base")


def doc(*rule_list: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"datasets": {"t": "t.csv"}, "rules": [{"dataset": "t", **r} for r in rule_list], **extra}


def test_a_complete_file_is_read(tmp_path: Path) -> None:
    (tmp_path / "r.yml").write_text(
        """
as_of: 2026-01-01
datasets:
  orders: data/orders.parquet
  people: {path: people.dat, format: csv}
rules:
  - {id: A, dataset: orders, type: not_null, column: id, severity: warn, max_failures: 3}
  - {dataset: orders, type: unique, columns: id}
  - {dataset: people, type: outliers, column: age}
""",
        encoding="utf-8",
    )
    rs = load_rules(tmp_path / "r.yml")
    assert rs.as_of == date(2026, 1, 1)
    assert rs.datasets["orders"].path == tmp_path.resolve() / "data/orders.parquet"
    assert rs.datasets["people"].format == "csv"
    assert [r.id for r in rs.rules] == ["A", "R02", "R03"]
    assert rs.rules[0].severity == "warn" and rs.rules[0].max_failures == 3
    assert rs.rules[1].params["columns"] == ["id"]  # a single name becomes a list
    assert rs.rules[2].params == {"column": "age", "method": "iqr", "threshold": 1.5}
    assert rs.rules[2].label() == "outliers(age)"


def test_defaults_per_method() -> None:
    z = parse_rules(doc({"type": "outliers", "column": "a", "method": "zscore"}), BASE).rules[0]
    assert z.params["threshold"] == 3.0


BAD = [
    ("not a mapping", ["x"], "must be a mapping"),
    ("no datasets", {"rules": [{}]}, "datasets: list at least one"),
    ("no rules", {"datasets": {"t": "t.csv"}}, "rules: list at least one"),
    ("bad dataset name", {"datasets": {"my data": "t.csv"}, "rules": [{}]}, "not a usable name"),
    ("bad extension", {"datasets": {"t": "t.txt"}, "rules": [{}]}, "cannot tell the format"),
    ("bad dataset form", {"datasets": {"t": ["t.csv"]}, "rules": [{}]}, "expected a path"),
    ("rule not a mapping", {"datasets": {"t": "t.csv"}, "rules": ["x"]}, "each rule is a mapping"),
    ("unknown type", doc({"type": "magic"}), "unknown type 'magic'"),
    ("no type", doc({}), "unknown type ''"),
    (
        "unknown dataset",
        {"datasets": {"t": "t.csv"}, "rules": [{"type": "row_count", "min": 1, "dataset": "x"}]},
        "'x' is not in datasets",
    ),
    (
        "unknown key",
        doc({"type": "not_null", "column": "a", "colum": "b"}),
        "unknown key(s) for a not_null rule: colum",
    ),
    ("missing key", doc({"type": "regex", "column": "a"}), "needs: pattern"),
    ("bad severity", doc({"type": "not_null", "column": "a", "severity": "fatal"}), "severity must be error or warn"),
    ("bad max_failures", doc({"type": "not_null", "column": "a", "max_failures": -1}), "max_failures"),
    ("bad max_failures type", doc({"type": "not_null", "column": "a", "max_failures": 1.5}), "max_failures"),
    ("bad rate", doc({"type": "not_null", "column": "a", "max_failure_rate": 2}), "between 0 and 1"),
    ("rate not a number", doc({"type": "not_null", "column": "a", "max_failure_rate": "x"}), "must be a number"),
    ("range without bounds", doc({"type": "range", "column": "a"}), "min, max or both"),
    ("range without column", doc({"type": "range", "min": 1}), "needs: column"),
    ("row_count without bounds", doc({"type": "row_count"}), "min, max or both"),
    ("row_count not whole", doc({"type": "row_count", "min": 1.5}), "whole number"),
    ("empty values", doc({"type": "allowed_values", "column": "a", "values": []}), "non-empty list"),
    ("empty pattern", doc({"type": "regex", "column": "a", "pattern": ""}), "non-empty string"),
    ("empty sql", doc({"type": "expression", "sql": " "}), "non-empty SQL"),
    ("bad method", doc({"type": "outliers", "column": "a", "method": "magic"}), "iqr or zscore"),
    ("bad threshold", doc({"type": "outliers", "column": "a", "threshold": 0}), "positive"),
    ("negative age", doc({"type": "freshness", "column": "a", "max_age_days": -1}), "0 or more"),
    ("unique bad columns", doc({"type": "unique", "columns": []}), "column name or a list"),
    (
        "fk unknown ref",
        doc({"type": "foreign_key", "column": "a", "ref_dataset": "x", "ref_column": "b"}),
        "ref_dataset 'x'",
    ),
    ("column not text", doc({"type": "not_null", "column": 5}), "column name"),
    (
        "duplicate id",
        doc({"id": "A", "type": "row_count", "min": 1}, {"id": "A", "type": "row_count", "min": 1}),
        "used twice",
    ),
    ("bad as_of", doc({"type": "row_count", "min": 1}, as_of="soon"), "as_of must be a date"),
    ("as_of type", doc({"type": "row_count", "min": 1}, as_of=5), "as_of must be a date"),
    ("extra top key", doc({"type": "row_count", "min": 1}, extra=1), "must be a mapping with datasets and rules"),
]


@pytest.mark.parametrize(("name", "raw", "message"), BAD, ids=[b[0] for b in BAD])
def test_every_mistake_has_a_message(name: str, raw: Any, message: str) -> None:
    with pytest.raises(ConfigError, match=message.replace("(", r"\(").replace(")", r"\)")):
        parse_rules(raw, BASE)


def test_unreadable_and_invalid_files(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read"):
        load_rules(tmp_path / "missing.yml")
    (tmp_path / "bad.yml").write_text("rules: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_rules(tmp_path / "bad.yml")


def test_every_type_is_described() -> None:
    assert set(rules.SPECS) == set(rules.DESCRIPTIONS)
    assert rules.ROW_LEVEL | {"row_count", "freshness"} == set(rules.SPECS)
