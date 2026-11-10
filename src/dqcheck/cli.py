"""The command line: run, profile, suggest, generate, bench, render, types."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import yaml

from dqcheck import __version__, bench, engine, gen, profile, report
from dqcheck.rules import DESCRIPTIONS, SPECS, ConfigError, load_rules


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dqcheck", description="Data quality checks from a YAML file, run on DuckDB.")
    p.add_argument("--version", action="version", version=f"dqcheck {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="run the rules of a file; exit 1 if a rule fails")
    run.add_argument("rules", type=Path, help="the rules file (YAML)")
    run.add_argument("--out", type=Path, help="write report.json and report.md here")
    run.add_argument("--sample", type=int, default=5, help="offending rows shown per rule (default 5)")
    run.add_argument("--as-of", type=date.fromisoformat, help="the date freshness is measured against (default: today)")
    run.add_argument("--strict", action="store_true", help="a warning also makes the exit code 1")
    run.add_argument("--threads", type=int, help="DuckDB threads (default: all cores)")

    prof = sub.add_parser("profile", help="describe the columns of CSV or Parquet files")
    prof.add_argument("files", type=Path, nargs="+")

    sug = sub.add_parser("suggest", help="write a first rules file from the data as it is now")
    sug.add_argument("files", type=Path, nargs="+")
    sug.add_argument("-o", "--output", type=Path, required=True)

    g = sub.add_parser("generate", help="write synthetic shop data with faults injected in known numbers")
    g.add_argument("--rows", type=int, default=10_000, help="orders (customers are a tenth of that)")
    g.add_argument("--seed", type=int, default=1)
    g.add_argument(
        "--error-rate", type=float, default=0.01, help=f"share of rows damaged per kind, up to {gen.MAX_RATE}"
    )
    g.add_argument("--format", choices=["parquet", "csv", "both"], default="parquet")
    g.add_argument("--out", type=Path, required=True)

    b = sub.add_parser("bench", help="time the rule set on generated data of growing size")
    b.add_argument("--sizes", default="100000,1000000", help="orders, comma separated")
    b.add_argument("--repeat", type=int, default=3)
    b.add_argument("--seed", type=int, default=1)
    b.add_argument("--threads", type=int)
    b.add_argument("--workdir", type=Path, help="where the generated files go while it runs (default: the temp folder)")
    b.add_argument("--out", type=Path, required=True)

    r = sub.add_parser("render", help="rebuild the Markdown (and the figure) from a report.json or bench.json")
    r.add_argument("report", type=Path)
    r.add_argument("-o", "--output", type=Path, required=True)
    r.add_argument("--figure", type=Path, help="for a benchmark: the summary figure")

    sub.add_parser("types", help="list the rule types and what each one checks")
    return p


def _cmd_run(args: argparse.Namespace) -> int:
    ruleset = load_rules(args.rules)
    start = time.perf_counter()
    with engine.Session(ruleset, args.threads) as session:
        outcomes = engine.run_all(session, args.sample, args.as_of)
        data = report.build_report(
            session, outcomes, "run " + " ".join(sys.argv[2:]), args.as_of, time.perf_counter() - start
        )
    print(report.render_text(data))
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "report.json").write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        (args.out / "report.md").write_text(report.render_markdown(data), encoding="utf-8")
        print(f"written: {args.out / 'report.json'}, {args.out / 'report.md'}")
    return engine.exit_code(outcomes, args.strict)


def _cmd_profile(args: argparse.Namespace) -> int:
    with engine.Session.from_files(args.files) as session:
        for name, table in session.tables.items():
            print(profile.render_profile(name, table.rows, profile.profile_table(session, name)))
    return 0


def _cmd_suggest(args: argparse.Namespace) -> int:
    with engine.Session.from_files(args.files) as session:
        document = profile.suggestion_document(session, args.output.parent)
    header = (
        "# A first set of rules written from the data as it is now. Read it before you trust it: it says what the\n"
        "# file contains today, not what it should contain. Delete what is accidental and tighten what matters.\n"
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(header + yaml.safe_dump(document, sort_keys=False, width=120), encoding="utf-8")
    print(f"written: {args.output} ({len(document['rules'])} rules)")
    return 0


def _cmd_generate(args: argparse.Namespace) -> int:
    try:
        generated = gen.generate(args.rows, args.seed, args.error_rate)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    paths = gen.write(generated, args.out, args.format)
    rows = generated.manifest["rows"]
    print(f"written: {len(paths)} files in {args.out} ({rows['orders']:,} orders, {rows['customers']:,} customers)")
    damaged = sum(len(v) for k, v in generated.faults.items())
    print(f"{damaged:,} damaged rows in {len(generated.faults)} kinds; manifest.json says which rule must find which")
    return 0


def _cmd_bench(args: argparse.Namespace) -> int:
    try:
        sizes = [int(s) for s in args.sizes.split(",") if s.strip()]
    except ValueError as exc:
        raise ConfigError("--sizes is a comma separated list of whole numbers") from exc
    if not sizes or min(sizes) < 200:
        raise ConfigError("--sizes needs at least one size, and each at least 200")
    data = bench.run_bench(
        sorted(sizes),
        args.repeat,
        args.seed,
        threads=args.threads,
        workdir=args.workdir,
        command="bench " + " ".join(sys.argv[2:]),
        progress=lambda m: print(m, file=sys.stderr, flush=True),
    )
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "bench.json").write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (args.out / "bench.md").write_text(report.render_bench_markdown(data), encoding="utf-8")
    report.bench_figure(data, args.out / "summary.png")
    print(f"written: {args.out / 'bench.json'}, {args.out / 'bench.md'}, {args.out / 'summary.png'}")
    if not all(s["verified"] for s in data["sizes"]):
        print(
            "a count differs from the manifest: " + json.dumps([s["mismatches"] for s in data["sizes"]]),
            file=sys.stderr,
        )
        return 1
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    try:
        data: dict[str, Any] = json.loads(args.report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot read {args.report}: {exc}") from exc
    if "sizes" in data:
        args.output.write_text(report.render_bench_markdown(data), encoding="utf-8")
        if args.figure:
            report.bench_figure(data, args.figure)
    else:
        args.output.write_text(report.render_markdown(data), encoding="utf-8")
    return 0


def _cmd_types(_args: argparse.Namespace) -> int:
    for name, (required, optional) in SPECS.items():
        keys = ", ".join(sorted(required)) + ("; optional: " + ", ".join(sorted(optional)) if optional else "")
        print(f"{name}\n    {DESCRIPTIONS[name]}\n    keys: {keys or '-'}")
    print("\nevery rule also takes: id, dataset, severity (error or warn), max_failures, max_failure_rate, description")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    handlers = {
        "run": _cmd_run,
        "profile": _cmd_profile,
        "suggest": _cmd_suggest,
        "generate": _cmd_generate,
        "bench": _cmd_bench,
        "render": _cmd_render,
        "types": _cmd_types,
    }
    try:
        return handlers[args.cmd](args)
    except ConfigError as exc:
        print(f"dqcheck: {exc}", file=sys.stderr)
        return 2
    except duckdb.Error as exc:
        print(f"dqcheck: DuckDB error: {exc}", file=sys.stderr)
        return 1
