"""The benchmark: generated data of growing size, the full rule set, and a check of every count against the manifest."""

from __future__ import annotations

import os
import statistics
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dqcheck import gen
from dqcheck.engine import Session, run_all
from dqcheck.report import environment
from dqcheck.rules import load_rules


def _load_ms(rules_path: Path, threads: int | None) -> tuple[float, Session]:
    start = time.perf_counter()
    session = Session(load_rules(rules_path), threads)
    return (time.perf_counter() - start) * 1000, session


def run_bench(
    sizes: list[int],
    repeat: int = 3,
    seed: int = 1,
    error_rate: float = 0.01,
    threads: int | None = None,
    workdir: Path | None = None,
    command: str = "bench",
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    say = progress or (lambda _m: None)
    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        for rows in sizes:
            say(f"{rows:,} orders: generating")
            generated = gen.generate(rows, seed, error_rate)
            folder = Path(tmp) / f"n{rows}"
            files = gen.write(generated, folder, "both")
            parquet_rules = folder / "rules.yml"
            csv_rules = folder / "rules_csv.yml"
            csv_rules.write_text(
                parquet_rules.read_text(encoding="utf-8").replace(".parquet", ".csv"), encoding="utf-8"
            )
            parquet_ms: list[float] = []
            csv_ms: list[float] = []
            say(f"{rows:,} orders: loading Parquet and CSV")
            for _ in range(repeat):
                t, session = _load_ms(csv_rules, threads)
                session.close()
                csv_ms.append(t)
            t, session = _load_ms(parquet_rules, threads)
            parquet_ms.append(t)
            for _ in range(repeat - 1):
                session.close()
                t, session = _load_ms(parquet_rules, threads)
                parquet_ms.append(t)
            say(f"{rows:,} orders: running the rules")
            per_rule: dict[str, list[float]] = {}
            totals: list[float] = []
            outcomes = []
            for _ in range(repeat):
                outcomes = run_all(session, sample=0)
                totals.append(sum(o.time_ms for o in outcomes))
                for o in outcomes:
                    per_rule.setdefault(o.rule.id, []).append(o.time_ms)
            expected = generated.manifest["expected"]
            mismatches = [
                o.rule.id
                for o in outcomes
                if o.status == "error" or (o.failing_rows or 0) != expected[o.rule.id]["failing_rows"]
                if o.failing_rows is not None
            ]
            customers = session.tables["customers"].rows
            check_ms = statistics.median(totals)
            results.append(
                {
                    "orders": rows,
                    "customers": customers,
                    "parquet_bytes": files["orders.parquet"].stat().st_size + files["customers.parquet"].stat().st_size,
                    "csv_bytes": files["orders.csv"].stat().st_size + files["customers.csv"].stat().st_size,
                    "load_parquet_ms": round(statistics.median(parquet_ms), 1),
                    "load_csv_ms": round(statistics.median(csv_ms), 1),
                    "check_ms": round(check_ms, 1),
                    "rows_per_s": (rows + customers) / (check_ms / 1000),
                    "verified": not mismatches,
                    "mismatches": mismatches,
                    "rules": {
                        o.rule.id: {
                            "dataset": o.rule.dataset,
                            "type": o.rule.type,
                            "check": o.rule.label(),
                            "failing_rows": o.failing_rows,
                            "median_ms": round(statistics.median(per_rule[o.rule.id]), 3),
                        }
                        for o in outcomes
                    },
                }
            )
            session.close()
    env = environment(
        {
            "cpus": os.cpu_count(),
            "threads": threads or os.cpu_count(),
            "seed": seed,
            "error_rate": error_rate,
            "repeat": repeat,
            "command": command,
            "seconds": round(time.perf_counter() - started, 1),
        }
    )
    return {"environment": env, "sizes": results}
