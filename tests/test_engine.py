"""Each rule type on the generated data, against the manifest of injected faults and against the Python reference."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from dqcheck import engine, gen, rules
from tests import reference as ref


def _amount_matches(r: dict[str, Any]) -> bool | None:
    if r["amount_cents"] is None:
        return None
    return bool(r["amount_cents"] == r["quantity"] * r["unit_cents"])


@pytest.fixture
def outcomes(data_dir: Path) -> dict[str, engine.Outcome]:
    with engine.Session(rules.load_rules(data_dir / "rules.yml")) as session:
        results = engine.run_all(session, sample=10**6)
    return {o.rule.id: o for o in results}


def _rows(outcome: engine.Outcome) -> list[int]:
    return [s["row"] for s in outcome.samples]


def test_every_rule_finds_exactly_the_injected_rows(
    outcomes: dict[str, engine.Outcome], generated: gen.Generated
) -> None:
    expected = generated.manifest["expected"]
    assert set(outcomes) == set(expected)
    for rule_id, outcome in outcomes.items():
        want = expected[rule_id]
        assert outcome.status != "error", (rule_id, outcome.message)
        if outcome.failing_rows is not None:
            assert outcome.failing_rows == want["failing_rows"], rule_id
            assert _rows(outcome) == want.get("rows", []), rule_id


def test_status_and_exit_code(outcomes: dict[str, engine.Outcome]) -> None:
    assert outcomes["C03"].status == "warn"  # severity warn
    assert outcomes["O01"].status == "fail"
    for rule_id in ("C01", "C02", "C05", "O10", "O11"):
        assert outcomes[rule_id].status == "pass"
    ordered = list(outcomes.values())
    assert engine.exit_code(ordered) == 1
    assert engine.exit_code([o for o in ordered if o.status in ("pass", "warn")]) == 0
    assert engine.exit_code([o for o in ordered if o.status in ("pass", "warn")], strict=True) == 1


def test_rules_agree_with_the_python_reference(data_dir: Path, outcomes: dict[str, engine.Outcome]) -> None:
    ext = "parquet" if (data_dir / "orders.parquet").exists() else "csv"
    customers = ref.read(data_dir / f"customers.{ext}")
    orders = ref.read(data_dir / f"orders.{ext}")
    cutoff = datetime(2026, 1, 1)
    from dqcheck.gen import COUNTRIES, EMAIL_PATTERN, STATUSES

    want = {
        "C01": ref.unique(customers, ["customer_id"]),
        "C02": ref.not_null(customers, "email"),
        "C03": ref.regex(customers, "email", EMAIL_PATTERN),
        "C04": ref.allowed_values(customers, "country", COUNTRIES),
        "C05": ref.value_range(customers, "signup_date", datetime(2020, 1, 1), cutoff),
        "O01": ref.unique(orders, ["order_id"]),
        "O02": ref.not_null(orders, "customer_id"),
        "O03": ref.foreign_key(orders, "customer_id", customers, "customer_id"),
        "O04": ref.not_null(orders, "amount_cents"),
        "O05": ref.value_range(orders, "amount_cents", 0, None),
        "O06": ref.allowed_values(orders, "status", STATUSES),
        "O07": ref.expression(orders, _amount_matches),
        "O08": ref.outliers_iqr(orders, "amount_cents", 3.0),
        "O09": ref.value_range(orders, "ordered_at", None, cutoff),
    }
    for rule_id, rows in want.items():
        assert set(_rows(outcomes[rule_id])) == rows, rule_id
        assert outcomes[rule_id].failing_rows == len(rows), rule_id


def test_the_outlier_rule_sees_only_the_injected_outliers(generated: gen.Generated) -> None:
    """Clean amounts stay inside the fences, so the rule has no false alarms (a property of the generator)."""
    amounts = [int(a) for a in generated.orders["amount_cents"].compressed()]
    q1, q3 = ref.quantile(sorted(amounts), 0.25), ref.quantile(sorted(amounts), 0.75)
    low, high = q1 - 3 * (q3 - q1), q3 + 3 * (q3 - q1)
    flagged = [a for a in amounts if a < low or a > high]
    assert len(flagged) == len(generated.faults["outlier_amount"])


def test_zscore_outliers_agree_with_the_reference(data_dir: Path) -> None:
    ext = "parquet" if (data_dir / "orders.parquet").exists() else "csv"
    orders = ref.read(data_dir / f"orders.{ext}")
    doc = {
        "datasets": {"orders": f"orders.{ext}"},
        "rules": [{"id": "Z", "dataset": "orders", "type": "outliers", "column": "amount_cents", "method": "zscore"}],
    }
    with engine.Session(rules.parse_rules(doc, data_dir)) as session:
        outcome = session.run(session.ruleset.rules[0], sample=10**6)
    expected = ref.outliers_zscore(orders, "amount_cents", 3.0)
    assert expected  # the injected outliers are far from the mean
    assert set(_rows(outcome)) == expected


def test_clean_data_passes_everything(tmp_path: Path) -> None:
    clean = gen.generate(1000, 3, 0.0)
    gen.write(clean, tmp_path, "parquet")
    with engine.Session(rules.load_rules(tmp_path / "rules.yml")) as session:
        results = engine.run_all(session)
    assert [o.status for o in results] == ["pass"] * 16
    assert all(not o.samples for o in results)
