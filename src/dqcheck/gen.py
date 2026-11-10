"""Synthetic data with faults injected in known numbers, and the rules file that finds them.

The shop has two datasets: customers and orders. A fraction of the rows of each is damaged in one of a dozen ways,
every kind on its own rows, so that each rule has an exact answer: the manifest lists, for every rule, how many rows
it must report and which ones.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import yaml

AS_OF = date(2026, 1, 1)
COUNTRIES = ["IT", "DE", "FR", "ES", "NL", "PL", "SE", "PT"]
STATUSES = ["paid", "shipped", "refunded", "cancelled", "pending"]
BAD_STATUSES = ["PAID", "shiped", "unknown", "done"]
BAD_COUNTRIES = ["XX", "usa", "Italy"]
BAD_EMAILS = ["not-an-email", "a@b", "x y@example.com", "@example.com"]
EMAIL_PATTERN = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
MAX_RATE = 0.05
START = np.datetime64("2023-01-01T00:00:00", "s")
SPAN_SECONDS = 3 * 365 * 24 * 3600  # clean orders fall in 2023, 2024 and 2025
MANIFEST_ROWS_LIMIT = 10_000

# fault -> the rule that must find it
ORDER_FAULTS = {
    "null_customer": "O02",
    "duplicate_order_id": "O01",
    "orphan_customer": "O03",
    "null_amount": "O04",
    "negative_amount": "O05",
    "bad_status": "O06",
    "mismatch_amount": "O07",
    "outlier_amount": "O08",
    "future_date": "O09",
}
CUSTOMER_FAULTS = {"bad_email": "C03", "bad_country": "C04"}


@dataclass
class Generated:
    customers: dict[str, Any]
    orders: dict[str, Any]
    manifest: dict[str, Any]
    rows: int = 0
    faults: dict[str, list[int]] = field(default_factory=dict)  # fault -> sorted 1-based rows


def _count(total: int, rate: float) -> int:
    return max(1, round(total * rate)) if rate > 0 else 0


def generate(rows: int, seed: int = 1, error_rate: float = 0.01) -> Generated:
    if rows < 200:
        raise ValueError("rows must be at least 200")
    if not 0 <= error_rate <= MAX_RATE:
        raise ValueError(f"error_rate must be between 0 and {MAX_RATE}")
    rng = np.random.default_rng(seed)
    n_customers = max(rows // 10, 50)

    # ---- customers
    customer_id = np.arange(1, n_customers + 1, dtype=np.int64)
    email = np.char.add(np.char.add("user", customer_id.astype(str)), "@example.com").astype(object)
    country = np.array(COUNTRIES, dtype=object)[rng.integers(0, len(COUNTRIES), n_customers)]
    signup = (np.datetime64("2020-01-01T00:00:00", "s") + rng.integers(0, 2000 * 86400, n_customers)).astype(
        "datetime64[s]"
    )

    kc = _count(n_customers, error_rate)
    cpick = rng.permutation(n_customers)
    bad_email_idx = np.sort(cpick[:kc])
    bad_country_idx = np.sort(cpick[kc : 2 * kc])
    email[bad_email_idx] = np.array(BAD_EMAILS, dtype=object)[rng.integers(0, len(BAD_EMAILS), len(bad_email_idx))]
    country[bad_country_idx] = np.array(BAD_COUNTRIES, dtype=object)[
        rng.integers(0, len(BAD_COUNTRIES), len(bad_country_idx))
    ]

    # ---- orders
    order_id = np.arange(1, rows + 1, dtype=np.int64)
    cust = rng.integers(1, n_customers + 1, rows).astype(np.int64)
    status = np.array(STATUSES, dtype=object)[rng.choice(len(STATUSES), rows, p=[0.55, 0.25, 0.08, 0.07, 0.05])]
    quantity = rng.integers(1, 6, rows).astype(np.int64)
    unit = rng.integers(500, 20001, rows).astype(np.int64)
    amount = quantity * unit
    ordered = (START + rng.integers(0, SPAN_SECONDS, rows)).astype("datetime64[s]")
    coupon = np.array(["SAVE10", "SAVE20", "FREESHIP"], dtype=object)[rng.integers(0, 3, rows)]
    coupon[rng.random(rows) < 0.8] = None

    k = _count(rows, error_rate)
    pick = rng.permutation(rows)
    cursor = 0

    def take(n: int) -> np.ndarray:
        nonlocal cursor
        chosen = np.sort(pick[cursor : cursor + n])
        cursor += n
        return chosen

    null_customer = take(k)
    null_amount = take(k)
    originals, copies = take(k), take(k)
    negative = take(k)
    bad_status = take(k)
    orphan = take(k)
    mismatch = take(k)
    outlier = take(k)
    future = take(k)

    cust_mask = np.zeros(rows, dtype=bool)
    cust_mask[null_customer] = True
    amount_mask = np.zeros(rows, dtype=bool)
    amount_mask[null_amount] = True
    order_id[copies] = order_id[originals]  # a copy takes the id of an earlier-or-later original
    unit[negative] = -(unit[negative] // 10)
    amount[negative] = quantity[negative] * unit[negative]  # small and negative: only the range rule sees it
    status[bad_status] = np.array(BAD_STATUSES, dtype=object)[rng.integers(0, len(BAD_STATUSES), len(bad_status))]
    cust[orphan] = n_customers + 1 + rng.integers(0, 1000, len(orphan))
    amount[mismatch] += rng.integers(1, 500, len(mismatch))  # quantity * unit no longer adds up
    amount[outlier] *= 1000
    unit[outlier] *= 1000  # consistent, but far from every other order
    ordered[future] = np.datetime64("2026-01-02T00:00:00", "s") + rng.integers(0, 365 * 86400, len(future))

    faults: dict[str, list[int]] = {
        "null_customer": [int(i) + 1 for i in null_customer],
        "null_amount": [int(i) + 1 for i in null_amount],
        "duplicate_order_id": sorted(int(i) + 1 for i in np.concatenate([originals, copies])),
        "negative_amount": [int(i) + 1 for i in negative],
        "bad_status": [int(i) + 1 for i in bad_status],
        "orphan_customer": [int(i) + 1 for i in orphan],
        "mismatch_amount": [int(i) + 1 for i in mismatch],
        "outlier_amount": [int(i) + 1 for i in outlier],
        "future_date": [int(i) + 1 for i in future],
        "bad_email": [int(i) + 1 for i in bad_email_idx],
        "bad_country": [int(i) + 1 for i in bad_country_idx],
    }
    expected: dict[str, Any] = {}
    for fault, rule in {**ORDER_FAULTS, **CUSTOMER_FAULTS}.items():
        entry: dict[str, Any] = {"fault": fault, "failing_rows": len(faults[fault])}
        if len(faults[fault]) <= MANIFEST_ROWS_LIMIT:
            entry["rows"] = faults[fault]
        expected[rule] = entry
    clean = {"C01", "C02", "C05", "O10", "O11"}
    for rule in sorted(clean):
        expected[rule] = {"fault": None, "failing_rows": 0}

    customers = {
        "customer_id": customer_id,
        "email": email,
        "country": country,
        "signup_date": signup,
    }
    orders = {
        "order_id": order_id,
        "customer_id": np.ma.masked_array(cust, mask=cust_mask),
        "status": status,
        "quantity": quantity,
        "unit_cents": unit,
        "amount_cents": np.ma.masked_array(amount, mask=amount_mask),
        "ordered_at": ordered,
        "coupon": coupon,
    }
    manifest = {
        "rows": {"customers": n_customers, "orders": rows},
        "seed": seed,
        "error_rate": error_rate,
        "as_of": AS_OF.isoformat(),
        "expected": dict(sorted(expected.items())),
    }
    return Generated(customers, orders, manifest, rows, faults)


def rules_document(formats: dict[str, str]) -> dict[str, Any]:
    """The rules file for the generated data. formats maps customers and orders to a file name."""
    return {
        "as_of": AS_OF.isoformat(),
        "datasets": {"customers": formats["customers"], "orders": formats["orders"]},
        "rules": [
            {"id": "C01", "dataset": "customers", "type": "unique", "columns": ["customer_id"]},
            {"id": "C02", "dataset": "customers", "type": "not_null", "column": "email"},
            {
                "id": "C03",
                "dataset": "customers",
                "type": "regex",
                "column": "email",
                "pattern": EMAIL_PATTERN,
                "severity": "warn",
                "description": "e-mail addresses look like addresses",
            },
            {"id": "C04", "dataset": "customers", "type": "allowed_values", "column": "country", "values": COUNTRIES},
            {
                "id": "C05",
                "dataset": "customers",
                "type": "range",
                "column": "signup_date",
                "min": "2020-01-01",
                "max": "2026-01-01",
            },
            {"id": "O01", "dataset": "orders", "type": "unique", "columns": ["order_id"]},
            {"id": "O02", "dataset": "orders", "type": "not_null", "column": "customer_id"},
            {
                "id": "O03",
                "dataset": "orders",
                "type": "foreign_key",
                "column": "customer_id",
                "ref_dataset": "customers",
                "ref_column": "customer_id",
            },
            {"id": "O04", "dataset": "orders", "type": "not_null", "column": "amount_cents"},
            {"id": "O05", "dataset": "orders", "type": "range", "column": "amount_cents", "min": 0},
            {"id": "O06", "dataset": "orders", "type": "allowed_values", "column": "status", "values": STATUSES},
            {
                "id": "O07",
                "dataset": "orders",
                "type": "expression",
                "sql": "amount_cents = quantity * unit_cents",
                "description": "an order's amount is quantity times unit price",
            },
            {
                "id": "O08",
                "dataset": "orders",
                "type": "outliers",
                "column": "amount_cents",
                "method": "iqr",
                "threshold": 3.0,
            },
            {"id": "O09", "dataset": "orders", "type": "range", "column": "ordered_at", "max": "2026-01-01"},
            {
                "id": "O10",
                "dataset": "orders",
                "type": "freshness",
                "column": "ordered_at",
                "max_age_days": 400,
            },
            {"id": "O11", "dataset": "orders", "type": "row_count", "min": 1},
        ],
    }


def write(generated: Generated, out: Path, fmt: str = "parquet") -> dict[str, Path]:
    """Write both datasets (parquet, csv or both), rules.yml and manifest.json; returns the paths by name."""
    if fmt not in ("parquet", "csv", "both"):
        raise ValueError("format must be parquet, csv or both")
    out.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(":memory:")
    paths: dict[str, Path] = {}
    tables = {"customers": generated.customers, "orders": generated.orders}
    for name, columns in tables.items():
        data = columns  # DuckDB finds the numpy columns through this variable name  # noqa: F841
        con.execute(f"CREATE TABLE {name} AS SELECT * FROM data")
        for ext in ("parquet", "csv") if fmt == "both" else (fmt,):
            target = out / f"{name}.{ext}"
            options = "FORMAT parquet" if ext == "parquet" else "FORMAT csv, HEADER"
            con.execute(f"COPY (SELECT * FROM {name}) TO ? ({options})", [str(target)])
            paths[f"{name}.{ext}"] = target
    con.close()
    main = "parquet" if fmt in ("parquet", "both") else "csv"
    rules = rules_document({n: f"{n}.{main}" for n in tables})
    (out / "rules.yml").write_text(
        "# Rules for the generated shop data; every rule is explained by 'dqcheck types'.\n"
        + yaml.safe_dump(rules, sort_keys=False, width=120),
        encoding="utf-8",
    )
    (out / "manifest.json").write_text(json.dumps(generated.manifest, indent=1) + "\n", encoding="utf-8")
    paths["rules.yml"] = out / "rules.yml"
    paths["manifest.json"] = out / "manifest.json"
    return paths
