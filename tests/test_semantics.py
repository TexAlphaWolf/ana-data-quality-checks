"""The corners of each rule, on small files made by hand: where a bound is inclusive, what a null does, what fails."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from dqcheck import engine, rules


def run(tmp_path: Path, csv: str, rule_list: list[dict[str, Any]], as_of: date | None = None) -> list[engine.Outcome]:
    (tmp_path / "t.csv").write_text(csv, encoding="utf-8")
    doc = {"datasets": {"t": "t.csv"}, "rules": [{"dataset": "t", **r} for r in rule_list]}
    with engine.Session(rules.parse_rules(doc, tmp_path)) as session:
        return engine.run_all(session, sample=100, as_of=as_of)


def rows(outcome: engine.Outcome) -> list[int]:
    return [s["row"] for s in outcome.samples]


def test_not_null_counts_nulls_and_an_empty_csv_field_is_null(tmp_path: Path) -> None:
    # DuckDB reads an empty field as null, quoted or not
    (out,) = run(tmp_path, 'a,b\n1,x\n,y\n3,""\n4,\n', [{"type": "not_null", "column": "b"}])
    assert rows(out) == [3, 4]
    (out,) = run(tmp_path, "a,b\n1,x\n,y\n3,z\n", [{"type": "not_null", "column": "a"}])
    assert rows(out) == [2]


def test_unique_on_one_and_on_two_columns_skips_nulls(tmp_path: Path) -> None:
    csv = "a,b\n1,x\n1,x\n1,y\n,z\n,z\n2,\n2,\n"
    one, two = run(
        tmp_path,
        csv,
        [{"id": "one", "type": "unique", "columns": "a"}, {"id": "two", "type": "unique", "columns": ["a", "b"]}],
    )
    assert rows(one) == [1, 2, 3, 6, 7]  # the two rows with a null a are not duplicates of each other
    assert rows(two) == [1, 2]  # (2, null) twice is skipped: a null in the key


def test_range_bounds_are_inclusive_and_nulls_are_skipped(tmp_path: Path) -> None:
    a, b, c = run(
        tmp_path,
        "n\n1\n5\n10\n11\n\n0.5\n",
        [
            {"id": "a", "type": "range", "column": "n", "min": 1, "max": 10},
            {"id": "b", "type": "range", "column": "n", "min": 5},
            {"id": "c", "type": "range", "column": "n", "max": 5.5},
        ],
    )
    assert rows(a) == [4, 6]
    assert rows(b) == [1, 6]
    assert rows(c) == [3, 4]


def test_range_on_dates_and_on_integers_with_a_fractional_bound(tmp_path: Path) -> None:
    d, i = run(
        tmp_path,
        "d,i\n2025-01-01,1\n2025-06-30,2\n2025-12-31,3\n",
        [
            {"id": "d", "type": "range", "column": "d", "min": date(2025, 2, 1), "max": "2025-12-30"},
            {"id": "i", "type": "range", "column": "i", "min": 1.5},
        ],
    )
    assert rows(d) == [1, 3]
    assert rows(i) == [1]  # 1.5 is not rounded to 2


def test_allowed_values_compares_text_and_skips_nulls(tmp_path: Path) -> None:
    text, num = run(
        tmp_path,
        "s,n\na,1\nb,2\nA,3\n,4\nc,\n",
        [
            {"id": "text", "type": "allowed_values", "column": "s", "values": ["a", "b"]},
            {"id": "num", "type": "allowed_values", "column": "n", "values": [1, 2, "3"]},
        ],
    )
    assert rows(text) == [3, 5]  # case matters
    assert rows(num) == [4]


def test_values_with_quotes_are_data_not_sql(tmp_path: Path) -> None:
    (out,) = run(
        tmp_path,
        's\nit\'s\nx\n"a;b"\n',
        [{"type": "allowed_values", "column": "s", "values": ["it's", "'); DROP TABLE t; --"]}],
    )
    assert rows(out) == [2, 3]


def test_regex_must_match_the_whole_value(tmp_path: Path) -> None:
    (out,) = run(
        tmp_path, "s\nab12\nab12x\n12\n\nAB12\n", [{"type": "regex", "column": "s", "pattern": "[a-z]+[0-9]+"}]
    )
    assert rows(out) == [2, 3, 5]


def test_foreign_key_skips_nulls_and_ignores_null_parents(tmp_path: Path) -> None:
    (tmp_path / "p.csv").write_text("id\n1\n2\n\n", encoding="utf-8")
    (tmp_path / "c.csv").write_text("p\n1\n3\n\n2\n9\n", encoding="utf-8")
    doc = {
        "datasets": {"p": "p.csv", "c": "c.csv"},
        "rules": [{"dataset": "c", "type": "foreign_key", "column": "p", "ref_dataset": "p", "ref_column": "id"}],
    }
    with engine.Session(rules.parse_rules(doc, tmp_path)) as session:
        out = engine.run_all(session, sample=100)[0]
    assert rows(out) == [2, 5]


def test_expression_null_counts_as_valid(tmp_path: Path) -> None:
    (out,) = run(tmp_path, "a,b\n1,1\n2,3\n,4\n5,\n", [{"type": "expression", "sql": "a = b"}])
    assert rows(out) == [2]


def test_iqr_fences_are_exclusive_of_the_boundary(tmp_path: Path) -> None:
    # 1..9 and 100: Q1 = 3.25, Q3 = 7.75, IQR 4.5; the upper fence at factor 1.5 is 14.5
    csv = "v\n" + "\n".join(str(x) for x in [1, 2, 3, 4, 5, 6, 7, 8, 9, 100]) + "\n"
    (out,) = run(tmp_path, csv, [{"type": "outliers", "column": "v"}])
    assert rows(out) == [10]


def test_zscore_ignores_a_constant_column(tmp_path: Path) -> None:
    (out,) = run(tmp_path, "v\n5\n5\n5\n", [{"type": "outliers", "column": "v", "method": "zscore"}])
    assert out.status == "pass"
    assert out.failing_rows == 0


def test_row_count_bounds(tmp_path: Path) -> None:
    ok, few, many = run(
        tmp_path,
        "a\n1\n2\n3\n",
        [
            {"id": "ok", "type": "row_count", "min": 3, "max": 3},
            {"id": "few", "type": "row_count", "min": 4},
            {"id": "many", "type": "row_count", "max": 2},
        ],
    )
    assert (ok.status, few.status, many.status) == ("pass", "fail", "fail")
    assert "at least 4" in few.message
    assert ok.failing_rows is None


def test_freshness_counts_days_before_as_of(tmp_path: Path) -> None:
    csv = "d\n2025-01-01\n2025-03-01\n"
    on_time, late = (
        run(
            tmp_path,
            csv,
            [{"id": "a", "type": "freshness", "column": "d", "max_age_days": 10}],
            as_of=date(2025, 3, 11),
        ),
        run(
            tmp_path,
            csv,
            [{"id": "a", "type": "freshness", "column": "d", "max_age_days": 10}],
            as_of=date(2025, 3, 12),
        ),
    )
    assert on_time[0].status == "pass"  # exactly 10 days is not late
    assert late[0].status == "fail"
    assert "11 days before 2025-03-12" in late[0].message


def test_freshness_of_a_column_with_no_values_fails(tmp_path: Path) -> None:
    (out,) = run(
        tmp_path, "d,x\n,1\n,2\n", [{"type": "freshness", "column": "d", "max_age_days": 1}], as_of=date(2025, 1, 1)
    )
    assert out.status == "fail"


def test_tolerance_is_a_count_or_a_share_whichever_is_larger(tmp_path: Path) -> None:
    csv = "a\n" + "\n".join(["1"] * 8 + ["", ""]) + "\n"  # 2 nulls in 10 rows
    none, by_count, by_rate, short_count, short_rate, neither = run(
        tmp_path,
        csv,
        [
            {"id": "none", "type": "not_null", "column": "a"},
            {"id": "by_count", "type": "not_null", "column": "a", "max_failures": 2},
            {"id": "by_rate", "type": "not_null", "column": "a", "max_failure_rate": 0.2},
            {"id": "short_count", "type": "not_null", "column": "a", "max_failures": 1},
            {"id": "short_rate", "type": "not_null", "column": "a", "max_failure_rate": 0.19},
            {"id": "neither", "type": "not_null", "column": "a", "max_failures": 1, "max_failure_rate": 0.1},
        ],
    )
    statuses = [o.status for o in (none, by_count, by_rate, short_count, short_rate, neither)]
    assert statuses == ["fail", "pass", "pass", "fail", "fail", "fail"]
    assert by_count.failing_rows == 2
    assert "within the tolerance" in by_count.message
    assert by_count.samples  # still reported


def test_a_rule_that_cannot_run_is_an_error_not_a_crash(tmp_path: Path) -> None:
    bad_col, bad_sql, bad_type, fine = run(
        tmp_path,
        "price,name\n1,a\n2,b\n",
        [
            {"id": "col", "type": "not_null", "column": "Price2"},
            {"id": "sql", "type": "expression", "sql": "nope > 1"},
            {"id": "type", "type": "range", "column": "price", "min": "abc"},
            {"id": "fine", "type": "not_null", "column": "price"},
        ],
    )
    assert bad_col.status == "error" and "did you mean price?" in bad_col.message
    assert bad_sql.status == "error" and "the query failed" in bad_sql.message
    assert bad_type.status == "error" and "must be numbers" in bad_type.message
    assert fine.status == "pass"  # the others still ran
    assert engine.exit_code([bad_col]) == 1


def test_column_names_are_case_insensitive_and_quoted(tmp_path: Path) -> None:
    (out,) = run(tmp_path, '"Weird ""name""",b\n1,2\n,3\n', [{"type": "not_null", "column": 'weird "name"'}])
    assert rows(out) == [2]
    assert list(out.samples[0]) == ["row", 'Weird "name"', "b"]


def test_a_column_name_cannot_carry_sql(tmp_path: Path) -> None:
    (out,) = run(tmp_path, "a\n1\n", [{"type": "not_null", "column": 'a" IS NULL OR 1=1 --'}])
    assert out.status == "error" and "no column" in out.message


def test_row_numbers_follow_the_file(tmp_path: Path) -> None:
    body = "\n".join(str(i) if i % 7 else "" for i in range(1, 200))
    (out,) = run(tmp_path, "a\n" + body + "\n", [{"type": "not_null", "column": "a"}])
    assert rows(out) == [i for i in range(1, 200) if i % 7 == 0]


def test_one_bad_value_makes_a_csv_column_text_and_a_numeric_range_then_refuses(tmp_path: Path) -> None:
    (tmp_path / "t.csv").write_text("id,qty\n1,5\n2,abc\n3,7\n", encoding="utf-8")
    doc = {
        "datasets": {"t": "t.csv"},
        "rules": [
            {"id": "range", "dataset": "t", "type": "range", "column": "qty", "min": 0},
            {"id": "digits", "dataset": "t", "type": "regex", "column": "qty", "pattern": "[0-9]+"},
        ],
    }
    with engine.Session(rules.parse_rules(doc, tmp_path)) as session:
        assert session.tables["t"].columns == {"id": "BIGINT", "qty": "VARCHAR"}
        bounded, digits = engine.run_all(session, sample=10)
    assert bounded.status == "error" and "is text" in bounded.message  # no silent comparison as text
    assert rows(digits) == [2]  # the regex finds the bad value


def test_all_varchar_keeps_the_text_as_written(tmp_path: Path) -> None:
    (tmp_path / "t.csv").write_text("price\n10.50\n3.25\n7.00\n12\n", encoding="utf-8")
    rule = {"id": "cents", "dataset": "t", "type": "regex", "column": "price", "pattern": r"[0-9]+\.[0-9]{2}"}
    results = {}
    for label, spec in (("number", "t.csv"), ("text", {"path": "t.csv", "all_varchar": True})):
        with engine.Session(rules.parse_rules({"datasets": {"t": spec}, "rules": [rule]}, tmp_path)) as session:
            results[label] = engine.run_all(session, sample=10)[0]
    assert rows(results["number"]) == [1, 3, 4]  # read as the numbers 10.5, 3.25, 7.0 and 12.0
    assert rows(results["text"]) == [4]  # as written: only 12 lacks the cents


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ({"path": "t.parquet", "all_varchar": True}, "CSV files only"),
        ({"path": "t.csv", "all_varchar": "yes"}, "true or false"),
        ({"path": "t.csv", "colour": 1}, "expected a path"),
    ],
)
def test_all_varchar_is_checked(spec: dict[str, Any], message: str) -> None:
    with pytest.raises(rules.ConfigError, match=re.escape(message)):
        rules.parse_rules(
            {"datasets": {"t": spec}, "rules": [{"dataset": "t", "type": "row_count", "min": 1}]}, Path(".")
        )
