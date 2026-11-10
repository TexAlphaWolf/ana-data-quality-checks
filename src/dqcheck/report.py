"""Reports from the results: JSON as the record, Markdown and a figure rebuilt from it."""

from __future__ import annotations

import platform
from datetime import date
from importlib import metadata
from pathlib import Path
from typing import Any

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from dqcheck import __version__
from dqcheck.engine import Outcome, Session

LABEL = {"pass": "PASS", "warn": "WARN", "fail": "FAIL", "error": "ERROR"}
BLUE, GREY, ORANGE = "#0072B2", "#8a8f98", "#D55E00"


def ms(value: float) -> str:
    return f"{value:.2f}" if value < 10 else f"{value:,.1f}"


def size(n: int) -> str:
    for unit, div in (("B", 1), ("KiB", 1024), ("MiB", 1024**2), ("GiB", 1024**3)):
        if n < div * 1024 or unit == "GiB":
            return f"{n:,} B" if unit == "B" else f"{n / div:.1f} {unit}"
    raise AssertionError("unreachable")


def environment(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    env: dict[str, Any] = {
        "dqcheck": __version__,
        "python": platform.python_version(),
        "duckdb": duckdb.__version__,
        "pyyaml": metadata.version("pyyaml"),
        "system": f"{platform.system()} {platform.machine()}",
    }
    env.update(extra or {})
    return env


def build_report(
    session: Session, outcomes: list[Outcome], command: str, as_of: date | None, seconds: float
) -> dict[str, Any]:
    threads = session.con.execute("SELECT current_setting('threads')").fetchone()
    counts = {k: sum(o.status == k for o in outcomes) for k in LABEL}
    return {
        "environment": environment(
            {
                "threads": int(threads[0]) if threads else None,
                "as_of": (as_of or session.ruleset.as_of or date.today()).isoformat(),
                "command": command,
                "seconds": round(seconds, 2),
            }
        ),
        "datasets": [
            {
                "name": t.name,
                "path": session.ruleset.datasets[t.name].path.name,
                "format": session.ruleset.datasets[t.name].format,
                "rows": t.rows,
                "columns": len(t.columns),
                "load_ms": round(t.load_ms, 1),
            }
            for t in session.tables.values()
        ],
        "summary": {"rules": len(outcomes), **counts},
        "rules": [
            {
                "id": o.rule.id,
                "type": o.rule.type,
                "check": o.rule.label(),
                "dataset": o.rule.dataset,
                "severity": o.rule.severity,
                "description": o.rule.description,
                "status": o.status,
                "failing_rows": o.failing_rows,
                "total_rows": o.total_rows,
                "rate": o.rate,
                "observed": o.observed,
                "message": o.message,
                "time_ms": round(o.time_ms, 3),
                "samples": o.samples,
            }
            for o in outcomes
        ],
    }


def _cell(value: Any) -> str:
    return "" if value is None else str(value).replace("|", "\\|").replace("\n", " ")


def render_markdown(report: dict[str, Any]) -> str:
    env, summary = report["environment"], report["summary"]
    out = ["# Data quality report", ""]
    out.append(
        f"Run on {env['system']}, Python {env['python']}, DuckDB {env['duckdb']} with {env['threads']} threads; "
        f"as of {env['as_of']}; {env['seconds']} s; command `{env['command']}`."
    )
    out += ["", "## Summary", ""]
    out.append(
        f"{summary['rules']} rules: {summary['pass']} passed, {summary['warn']} warnings, {summary['fail']} failed, "
        f"{summary['error']} could not run."
    )
    out += ["", "## Datasets", "", "| Dataset | File | Rows | Columns | Load (ms) |", "|---|---|---|---|---|"]
    for d in report["datasets"]:
        out.append(f"| {d['name']} | {d['path']} | {d['rows']:,} | {d['columns']} | {ms(d['load_ms'])} |")
    out += [
        "",
        "## Rules",
        "",
        "| Rule | Dataset | Check | Severity | Result | Failing rows | Share | Time (ms) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in report["rules"]:
        failing = "" if r["failing_rows"] is None else f"{r['failing_rows']:,}"
        share = "" if r["rate"] is None else f"{r['rate']:.2%}"
        out.append(
            f"| {r['id']} | {r['dataset']} | {_cell(r['check'])} | {r['severity']} | {LABEL[r['status']]} | {failing} | {share} | {ms(r['time_ms'])} |"
        )
    findings = [r for r in report["rules"] if r["status"] != "pass" or r["failing_rows"]]
    if findings:
        out += ["", "## Findings", ""]
    for r in findings:
        out += [f"### {r['id']} {_cell(r['check'])} on {r['dataset']}: {LABEL[r['status']]}", ""]
        if r["description"]:
            out += [r["description"], ""]
        out += [r["message"] + ".", ""]
        if r["samples"]:
            names = list(r["samples"][0])
            out += ["First offending rows (row is the 1-based position in the file, header excluded):", ""]
            out += ["| " + " | ".join(names) + " |", "|" + "---|" * len(names)]
            out += ["| " + " | ".join(_cell(s[n]) for n in names) + " |" for s in r["samples"]]
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def render_text(report: dict[str, Any]) -> str:
    """The short form printed by `dqcheck run`."""
    lines = []
    for r in report["rules"]:
        lines.append(f"{LABEL[r['status']]:<5} {r['id']:<5} {r['dataset']}.{r['check']}: {r['message']}")
    s = report["summary"]
    lines.append(
        f"{s['rules']} rules: {s['pass']} passed, {s['warn']} warnings, {s['fail']} failed, {s['error']} could not run"
    )
    return "\n".join(lines)


# ---- the benchmark ----


def render_bench_markdown(bench: dict[str, Any]) -> str:
    env = bench["environment"]
    out = ["# Benchmark", ""]
    out.append(
        f"Run on {env['system']}, {env['cpus']} CPUs, Python {env['python']}, DuckDB {env['duckdb']} with {env['threads']} threads; "
        f"seed {env['seed']}, {env['error_rate']:.0%} of the rows damaged; each timing is the median of {env['repeat']} runs; "
        f"{env['seconds']} s; command `{env['command']}`."
    )
    out += [
        "",
        "## 1. Scaling",
        "",
        "The whole rule set (16 rules over two datasets) on the loaded data, and the time to load the files.",
        "",
        "| Orders | Customers | Parquet | CSV | Load Parquet (ms) | Load CSV (ms) | All rules (ms) | Rows per second | Counts match the manifest |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in bench["sizes"]:
        out.append(
            f"| {s['orders']:,} | {s['customers']:,} | {size(s['parquet_bytes'])} | {size(s['csv_bytes'])} | "
            f"{ms(s['load_parquet_ms'])} | {ms(s['load_csv_ms'])} | {ms(s['check_ms'])} | {s['rows_per_s']:,.0f} | "
            f"{'yes' if s['verified'] else 'NO'} |"
        )
    big = bench["sizes"][-1]
    out += [
        "",
        f"## 2. Each rule at {big['orders']:,} orders",
        "",
        "| Rule | Dataset | Check | Failing rows | Median (ms) |",
        "|---|---|---|---|---|",
    ]
    for rule_id, r in big["rules"].items():
        failing = "" if r["failing_rows"] is None else f"{r['failing_rows']:,}"
        out.append(f"| {rule_id} | {r['dataset']} | {_cell(r['check'])} | {failing} | {ms(r['median_ms'])} |")
    return "\n".join(out) + "\n"


def bench_figure(bench: dict[str, Any], path: Path) -> None:
    sizes = bench["sizes"]
    fig, (left, right) = plt.subplots(1, 2, figsize=(13, 5.2), gridspec_kw={"width_ratios": [1, 1.15]})
    x = [s["orders"] for s in sizes]
    left.plot(x, [s["check_ms"] for s in sizes], "o-", color=BLUE, label="all 16 rules")
    left.plot(x, [s["load_parquet_ms"] for s in sizes], "s--", color=GREY, label="load Parquet")
    left.plot(x, [s["load_csv_ms"] for s in sizes], "^--", color=ORANGE, label="load CSV")
    left.set_xscale("log")
    left.set_yscale("log")
    left.set_xlabel("orders (customers are a tenth of that)")
    left.set_ylabel("median time (ms, log scale)")
    left.set_title("Checking and loading, by size")
    left.grid(alpha=0.3)
    left.legend(loc="upper left", fontsize=9)
    big = sizes[-1]
    items = sorted(big["rules"].items(), key=lambda kv: kv[1]["median_ms"])
    labels = [f"{k}  {v['check']}" for k, v in items]
    right.barh(labels, [v["median_ms"] for _, v in items], color=BLUE)
    for i, (_, v) in enumerate(items):
        right.text(v["median_ms"], i, f" {ms(v['median_ms'])}", va="center", fontsize=8)
    right.set_xlabel("median time (ms)")
    right.set_title(f"Each rule at {big['orders']:,} orders")
    right.tick_params(axis="y", labelsize=8)
    right.set_xlim(0, max(v["median_ms"] for _, v in items) * 1.15)
    fig.suptitle(f"dqcheck {bench['environment']['dqcheck']} on DuckDB {bench['environment']['duckdb']}")
    fig.tight_layout()
    fig.savefig(path, dpi=100)
    plt.close(fig)
