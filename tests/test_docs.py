"""The documentation must say what the measurements say."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from dqcheck import cli

ROOT = Path(__file__).resolve().parent.parent
BENCH = json.loads((ROOT / "docs" / "bench.json").read_text(encoding="utf-8"))
README = (ROOT / "README.md").read_text(encoding="utf-8")
ANALYSIS = (ROOT / "docs" / "analysis.md").read_text(encoding="utf-8")
SIZES = {s["orders"]: s for s in BENCH["sizes"]}


def test_every_count_was_verified() -> None:
    assert all(s["verified"] and not s["mismatches"] for s in BENCH["sizes"])


def test_bench_markdown_is_the_render_of_the_json(tmp_path: Path) -> None:
    out = tmp_path / "again.md"
    assert cli.main(["render", str(ROOT / "docs" / "bench.json"), "-o", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == (ROOT / "docs" / "bench.md").read_text(encoding="utf-8")


def test_figure_exists() -> None:
    assert (ROOT / "docs" / "summary.png").stat().st_size > 10_000


@pytest.mark.parametrize("text", [README, ANALYSIS], ids=["README", "analysis"])
def test_no_scaffold_left(text: str) -> None:
    assert not re.search(r"work in progress|TODO|TBD|lorem", text, re.I)


@pytest.mark.parametrize("name", ["README.md", "docs/analysis.md"])
def test_local_links_resolve(name: str) -> None:
    path = ROOT / name
    for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", path.read_text(encoding="utf-8")):
        if target.startswith(("http://", "https://")):
            continue
        assert (path.parent / target).exists(), f"{name}: broken link {target}"


def fmt(x: float, places: int = 1) -> str:
    return f"{x:,.{places}f}"


def test_numbers_in_readme_and_analysis() -> None:
    small, mid, big = SIZES[100_000], SIZES[1_000_000], SIZES[10_000_000]
    assert BENCH["environment"]["seconds"] == 87.4
    for text in (README, ANALYSIS):
        assert fmt(small["check_ms"]) in text
        assert fmt(big["check_ms"]) in text
        assert f"{round(big['rows_per_s']):,}" in text
    assert f"{round(mid['rows_per_s']):,}" in ANALYSIS
    assert fmt(mid["check_ms"]) in ANALYSIS
    assert fmt(big["load_csv_ms"]) in ANALYSIS
    # derived claims
    assert f"{big['check_ms'] / small['check_ms']:.1f} times" in README
    assert round(big["load_csv_ms"] / big["load_parquet_ms"]) == 21
    assert round(big["load_csv_ms"] / big["check_ms"]) == 15
    assert f"{big['csv_bytes'] / big['parquet_bytes']:.1f} times" in README
    assert f"{big['parquet_bytes'] / 2**20:.1f} MiB" in README
    assert f"{big['csv_bytes'] / 2**20:.1f} MiB" in README
    assert f"{big['check_ms'] / big['load_parquet_ms']:.1f} times" in ANALYSIS


def test_heavy_rules_claim() -> None:
    rules = SIZES[10_000_000]["rules"]
    ranked = sorted(rules, key=lambda r: rules[r]["median_ms"], reverse=True)
    assert ranked[:3] == ["O08", "O03", "O01"]
    share = sum(rules[r]["median_ms"] for r in ranked[:3]) / sum(r["median_ms"] for r in rules.values())
    assert round(share * 100) == 86
    for rid in ranked[:3]:
        assert fmt(rules[rid]["median_ms"]) in README
    assert rules["O01"]["failing_rows"] == 200_000 and "200,000" in README


def test_readme_output_matches_a_real_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["generate", "--rows", "10000", "--out", str(tmp_path)]) == 0
    capsys.readouterr()
    assert cli.main(["run", str(tmp_path / "rules.yml")]) == 1
    lines = capsys.readouterr().out.strip().splitlines()
    block = re.search(r"```text\n(.*?)```", README, re.S)
    assert block is not None
    assert [x.rstrip() for x in block.group(1).strip().splitlines()] == [x.rstrip() for x in lines]
