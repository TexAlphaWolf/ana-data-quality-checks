from __future__ import annotations

import json
from pathlib import Path

import pytest

from dqcheck import gen


def test_same_seed_same_data_and_a_different_seed_differs() -> None:
    a, b, c = gen.generate(1000, 5), gen.generate(1000, 5), gen.generate(1000, 6)
    assert a.faults == b.faults and a.manifest == b.manifest
    assert (a.orders["amount_cents"].data == b.orders["amount_cents"].data).all()
    assert a.faults != c.faults


@pytest.mark.parametrize("rows", [200, 1000, 20_000])
def test_faults_are_counted_and_do_not_overlap(rows: int) -> None:
    g = gen.generate(rows, 3, 0.02)
    k = round(rows * 0.02)
    order_kinds = [f for f in g.faults if f in gen.ORDER_FAULTS]
    assert {len(g.faults[f]) for f in order_kinds if f != "duplicate_order_id"} == {k}
    assert len(g.faults["duplicate_order_id"]) == 2 * k
    seen: set[int] = set()
    for kind in order_kinds:
        rows_of_kind = set(g.faults[kind])
        assert not rows_of_kind & seen, kind  # every kind has its own rows
        seen |= rows_of_kind
    customers = g.manifest["rows"]["customers"]
    assert not set(g.faults["bad_email"]) & set(g.faults["bad_country"])
    assert len(g.faults["bad_email"]) == max(1, round(customers * 0.02))


def test_the_manifest_names_a_rule_for_every_fault_and_the_clean_ones() -> None:
    g = gen.generate(1000, 1)
    expected = g.manifest["expected"]
    assert set(expected) == {f"C0{i}" for i in range(1, 6)} | {f"O{i:02d}" for i in range(1, 12)}
    assert {e["fault"] for e in expected.values()} - {None} == set(gen.ORDER_FAULTS) | set(gen.CUSTOMER_FAULTS)


def test_long_row_lists_are_left_out_of_the_manifest() -> None:
    g = gen.generate(200_000, 1, 0.01)
    entry = g.manifest["expected"]["O02"]
    assert entry["failing_rows"] == 2000 and "rows" in entry
    big = g.manifest["expected"]["O01"]
    assert big["failing_rows"] == 4000 and "rows" in big
    huge = gen.generate(200_000, 1, 0.05).manifest["expected"]["O02"]
    assert huge["failing_rows"] == 10_000 and "rows" in huge
    assert "rows" not in gen.generate(300_000, 1, 0.05).manifest["expected"]["O02"]


@pytest.mark.parametrize(("rows", "rate"), [(100, 0.01), (1000, -0.1), (1000, 0.5)])
def test_bad_arguments(rows: int, rate: float) -> None:
    with pytest.raises(ValueError, match=r"rows must|error_rate must"):
        gen.generate(rows, 1, rate)


def test_write_formats_and_the_rules_file(tmp_path: Path) -> None:
    g = gen.generate(500, 1)
    both = gen.write(g, tmp_path / "both", "both")
    assert {p.name for p in both.values()} == {
        "customers.parquet",
        "customers.csv",
        "orders.parquet",
        "orders.csv",
        "rules.yml",
        "manifest.json",
    }
    csv_only = gen.write(g, tmp_path / "csv", "csv")
    assert "orders.parquet" not in {p.name for p in csv_only.values()}
    assert "orders.csv" in (tmp_path / "csv" / "rules.yml").read_text(encoding="utf-8")
    assert json.loads((tmp_path / "csv" / "manifest.json").read_text(encoding="utf-8"))["seed"] == 1
    with pytest.raises(ValueError, match="format must be"):
        gen.write(g, tmp_path / "x", "xml")


def test_clean_amounts_stay_inside_the_fences_for_many_seeds() -> None:
    """The property the outlier rule's exact answer depends on."""
    for seed in range(1, 9):
        g = gen.generate(3000, seed, 0.02)
        amounts = sorted(int(a) for a in g.orders["amount_cents"].compressed())
        q1, q3 = amounts[len(amounts) // 4], amounts[3 * len(amounts) // 4]
        low, high = q1 - 3 * (q3 - q1), q3 + 3 * (q3 - q1)
        outside = sum(1 for a in amounts if a < low or a > high)
        assert outside == len(g.faults["outlier_amount"]), seed
