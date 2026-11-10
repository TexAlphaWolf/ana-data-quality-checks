from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from dqcheck import cli, engine, gen, rules


@pytest.fixture(scope="module")
def shop(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("shop")
    assert cli.main(["generate", "--rows", "2000", "--seed", "4", "--format", "both", "--out", str(folder)]) == 0
    return folder


def test_run_reports_failures_with_exit_1_and_writes_both_files(
    shop: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    assert cli.main(["run", str(shop / "rules.yml"), "--out", str(out), "--sample", "3"]) == 1
    text = capsys.readouterr().out
    assert "FAIL  O01" in text and "WARN  C03" in text and "PASS  C01" in text
    assert "16 rules: 5 passed, 1 warnings, 10 failed, 0 could not run" in text
    data = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert data["summary"] == {"rules": 16, "pass": 5, "warn": 1, "fail": 10, "error": 0}
    assert all(len(r["samples"]) <= 3 for r in data["rules"])
    assert data["environment"]["as_of"] == "2026-01-01"
    assert data["datasets"][1]["rows"] == 2000
    md = (out / "report.md").read_text(encoding="utf-8")
    assert "## Findings" in md and "### O01 unique(order_id) on orders: FAIL" in md
    assert "First offending rows" in md


def test_render_rebuilds_the_report_identically(shop: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    cli.main(["run", str(shop / "rules.yml"), "--out", str(out)])
    again = tmp_path / "again.md"
    assert cli.main(["render", str(out / "report.json"), "-o", str(again)]) == 0
    assert again.read_text(encoding="utf-8") == (out / "report.md").read_text(encoding="utf-8")


def test_a_clean_dataset_exits_0_and_strict_turns_warnings_into_failures(tmp_path: Path) -> None:
    gen.write(gen.generate(1000, 2, 0.0), tmp_path, "parquet")
    assert cli.main(["run", str(tmp_path / "rules.yml")]) == 0
    only_warning = yaml.safe_load((tmp_path / "rules.yml").read_text(encoding="utf-8"))
    only_warning["rules"].append({"id": "W", "dataset": "orders", "type": "row_count", "max": 10, "severity": "warn"})
    (tmp_path / "w.yml").write_text(yaml.safe_dump(only_warning), encoding="utf-8")
    assert cli.main(["run", str(tmp_path / "w.yml")]) == 0
    assert cli.main(["run", str(tmp_path / "w.yml"), "--strict"]) == 1


def test_as_of_decides_freshness(shop: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli.main(["run", str(shop / "rules.yml"), "--as-of", "2030-01-01"])
    assert "FAIL  O10" in capsys.readouterr().out


def test_threads_option(shop: Path) -> None:
    assert cli.main(["run", str(shop / "rules.yml"), "--threads", "1"]) == 1


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["run", "nope.yml"], "cannot read"),
        (["generate", "--rows", "10", "--out", "x"], "rows must be at least 200"),
        (["bench", "--sizes", "a,b", "--out", "x"], "comma separated"),
        (["bench", "--sizes", "10", "--out", "x"], "at least 200"),
        (["render", "nope.json", "-o", "x.md"], "cannot read"),
        (["profile", "nope.csv"], "does not exist"),
        (["profile", "data.txt"], "use a .csv or .parquet"),
    ],
)
def test_user_errors_exit_2_with_a_message(
    argv: list[str], message: str, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert cli.main(argv) == 2
    assert message in capsys.readouterr().err


def test_a_missing_dataset_file_and_an_unreadable_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "r.yml").write_text(
        yaml.safe_dump({"datasets": {"t": "t.csv"}, "rules": [{"dataset": "t", "type": "row_count", "min": 1}]}),
        encoding="utf-8",
    )
    assert cli.main(["run", str(tmp_path / "r.yml")]) == 2
    assert "t.csv does not exist" in capsys.readouterr().err
    (tmp_path / "t.csv").write_bytes(b"a,b\n1,2,3,4\n\xff\xfe\n")
    (tmp_path / "bad.parquet").write_bytes(b"not parquet")
    (tmp_path / "p.yml").write_text(
        yaml.safe_dump({"datasets": {"t": "bad.parquet"}, "rules": [{"dataset": "t", "type": "row_count", "min": 1}]}),
        encoding="utf-8",
    )
    assert cli.main(["run", str(tmp_path / "p.yml")]) == 2
    assert "cannot read bad.parquet" in capsys.readouterr().err


def test_types_lists_every_rule_type(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["types"]) == 0
    out = capsys.readouterr().out
    assert all(t in out for t in rules.SPECS)
    assert "max_failure_rate" in out


def test_generate_says_what_it_wrote(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["generate", "--rows", "1000", "--out", str(tmp_path / "g")]) == 0
    out = capsys.readouterr().out
    assert "1,000 orders, 100 customers" in out and "manifest.json" in out


def test_profile_describes_the_columns(shop: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["profile", str(shop / "orders.csv"), str(shop / "customers.parquet")]) == 0
    out = capsys.readouterr().out
    assert "### orders (2,000 rows)" in out and "### customers (200 rows)" in out
    line = next(line for line in out.splitlines() if line.startswith("| coupon"))
    assert "VARCHAR" in line and "| 3 |" in line  # three coupon codes
    assert "| order_id | BIGINT | 0 (0.0%) | 1,980 |" in out  # the injected duplicates are visible


def test_suggested_rules_pass_on_the_data_they_came_from(tmp_path: Path) -> None:
    gen.write(gen.generate(1000, 2, 0.0), tmp_path / "d", "parquet")
    target = tmp_path / "rules" / "suggested.yml"
    assert (
        cli.main(
            [
                "suggest",
                str(tmp_path / "d" / "orders.parquet"),
                str(tmp_path / "d" / "customers.parquet"),
                "-o",
                str(target),
            ]
        )
        == 0
    )
    document = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert document["datasets"]["orders"] == "../d/orders.parquet"
    kinds = {(r["dataset"], r["type"], r.get("column") or tuple(r.get("columns", ()))) for r in document["rules"]}
    assert ("orders", "unique", ("order_id",)) in kinds
    assert ("orders", "allowed_values", "status") in kinds
    assert ("orders", "not_null", "coupon") not in kinds  # the coupon column has nulls
    assert ("orders", "range", "amount_cents") in kinds
    assert not any(r["type"] == "unique" and r.get("columns") == ["amount_cents"] for r in document["rules"])
    assert cli.main(["run", str(target)]) == 0
    # ... and it notices when the data goes wrong afterwards
    gen.write(gen.generate(1000, 2, 0.02), tmp_path / "d", "parquet")
    assert cli.main(["run", str(target)]) == 1


def test_suggest_leaves_out_what_the_data_does_not_support(tmp_path: Path) -> None:
    (tmp_path / "tiny.csv").write_text("a,b,c\n1,x,1.5\n1,x,1.5\n", encoding="utf-8")
    out = tmp_path / "s.yml"
    assert cli.main(["suggest", str(tmp_path / "tiny.csv"), "-o", str(out)]) == 0
    types = [
        (r["type"], r.get("column") or r.get("columns"))
        for r in yaml.safe_load(out.read_text(encoding="utf-8"))["rules"]
    ]
    assert ("unique", ["a"]) not in types  # not unique
    assert ("range", "a") not in types  # a single value
    assert ("allowed_values", "b") not in types  # too few rows to call it a list
    assert ("not_null", "a") in types


def test_suggest_on_an_empty_file_writes_no_rules(tmp_path: Path) -> None:
    (tmp_path / "e.csv").write_text("a,b\n", encoding="utf-8")
    with engine.Session.from_files([tmp_path / "e.csv"]) as session:
        from dqcheck import profile

        assert profile.suggest_rules(session, "e") == []


def test_two_files_with_the_same_name_are_refused(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "x.csv").write_text("a\n1\n", encoding="utf-8")
    (tmp_path / "b" / "x.csv").write_text("a\n1\n", encoding="utf-8")
    with pytest.raises(rules.ConfigError, match="two files are called x"):
        engine.Session.from_files([tmp_path / "a" / "x.csv", tmp_path / "b" / "x.csv"])


def test_file_names_become_dataset_names(tmp_path: Path) -> None:
    (tmp_path / "2024-sales.csv").write_text("a\n1\n", encoding="utf-8")
    with engine.Session.from_files([tmp_path / "2024-sales.csv"]) as session:
        assert list(session.tables) == ["_2024_sales"]


def test_bench_runs_verifies_and_renders(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "bench"
    work = tmp_path / "work"
    work.mkdir()
    assert cli.main(["bench", "--sizes", "600,300", "--repeat", "2", "--workdir", str(work), "--out", str(out)]) == 0
    assert list(work.iterdir()) == []  # the generated files are removed
    assert "300 orders: generating" in capsys.readouterr().err
    data = json.loads((out / "bench.json").read_text(encoding="utf-8"))
    assert [s["orders"] for s in data["sizes"]] == [300, 600]
    assert all(s["verified"] and not s["mismatches"] for s in data["sizes"])
    assert set(data["sizes"][0]["rules"]) == {f"C0{i}" for i in range(1, 6)} | {f"O{i:02d}" for i in range(1, 12)}
    assert data["environment"]["repeat"] == 2
    assert (out / "summary.png").stat().st_size > 10_000
    again = tmp_path / "again.md"
    assert cli.main(["render", str(out / "bench.json"), "-o", str(again), "--figure", str(tmp_path / "f.png")]) == 0
    assert again.read_text(encoding="utf-8") == (out / "bench.md").read_text(encoding="utf-8")
    assert (tmp_path / "f.png").exists()
    assert "Counts match the manifest" in again.read_text(encoding="utf-8")


def test_bench_exits_1_when_a_count_differs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = engine.Session.run

    def lying(self: engine.Session, rule: rules.Rule, sample: int = 5, as_of: object = None) -> engine.Outcome:
        outcome = real(self, rule, sample, as_of)  # type: ignore[arg-type]
        if rule.id == "O02" and outcome.failing_rows is not None:
            outcome.failing_rows += 1
        return outcome

    monkeypatch.setattr(engine.Session, "run", lying)
    assert cli.main(["bench", "--sizes", "300", "--repeat", "1", "--out", str(tmp_path)]) == 1


def test_a_duckdb_error_is_a_message_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import duckdb

    def boom(_args: object) -> int:
        raise duckdb.Error("internal trouble")

    monkeypatch.setattr(cli, "_cmd_types", boom)
    assert cli.main(["types"]) == 1
    assert "DuckDB error: internal trouble" in capsys.readouterr().err


def test_python_dash_m_works(shop: Path) -> None:
    import subprocess
    import sys

    proc = subprocess.run([sys.executable, "-m", "dqcheck", "--version"], capture_output=True, text=True, check=True)
    assert proc.stdout.startswith("dqcheck 1.0.0")
