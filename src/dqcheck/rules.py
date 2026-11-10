"""The rules file: what it may contain, and its validation. Nothing here touches a database."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SEVERITIES = ("error", "warn")
COMMON = {"id", "type", "dataset", "severity", "max_failures", "max_failure_rate", "description"}

# type -> (required keys, optional keys). Keys that name a column are checked against the data when the rule runs.
SPECS: dict[str, tuple[set[str], set[str]]] = {
    "not_null": ({"column"}, set()),
    "unique": ({"columns"}, set()),
    "range": (set(), {"column", "min", "max"}),
    "allowed_values": ({"column", "values"}, set()),
    "regex": ({"column", "pattern"}, set()),
    "foreign_key": ({"column", "ref_dataset", "ref_column"}, set()),
    "outliers": ({"column"}, {"method", "threshold"}),
    "expression": ({"sql"}, set()),
    "row_count": (set(), {"min", "max"}),
    "freshness": ({"column", "max_age_days"}, set()),
}
DESCRIPTIONS = {
    "not_null": "rows where the column is null",
    "unique": "rows whose key (one or more columns) also appears in another row; rows with a null in the key are skipped",
    "range": "rows whose value is below min or above max; nulls are skipped",
    "allowed_values": "rows whose value, as text, is not in the list; nulls are skipped",
    "regex": "rows whose value, as text, does not match the whole pattern (RE2 syntax); nulls are skipped",
    "foreign_key": "rows whose value has no match in ref_dataset.ref_column; nulls are skipped",
    "outliers": "rows outside the IQR fences (method iqr, threshold = factor, default 1.5) or more than threshold "
    "standard deviations from the mean (method zscore, default 3); nulls are skipped",
    "expression": "rows where the SQL condition (which describes a valid row) is false; null counts as valid",
    "row_count": "the dataset has fewer than min or more than max rows (a whole-dataset check)",
    "freshness": "the newest value of the column is older than max_age_days before as_of (a whole-dataset check)",
}
ROW_LEVEL = {"not_null", "unique", "range", "allowed_values", "regex", "foreign_key", "outliers", "expression"}


class ConfigError(Exception):
    """A problem with the rules file or the data it points to: reported as a message, exit code 2."""


@dataclass(frozen=True)
class Rule:
    id: str
    type: str
    dataset: str
    severity: str = "error"
    max_failures: int = 0
    max_failure_rate: float = 0.0
    description: str = ""
    params: dict[str, Any] = field(default_factory=dict)

    def label(self) -> str:
        cols = self.params.get("columns") or self.params.get("column")
        where = ", ".join(cols) if isinstance(cols, list) else cols
        return f"{self.type}({where})" if where else self.type


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    path: Path
    format: str
    all_varchar: bool = False  # CSV only: read every column as text, so that a bad value is a finding, not a load error


@dataclass(frozen=True)
class RuleSet:
    datasets: dict[str, DatasetSpec]
    rules: list[Rule]
    as_of: date | None = None


def _fail(where: str, message: str) -> ConfigError:
    return ConfigError(f"{where}: {message}")


def _number(value: Any, where: str, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _fail(where, f"{key} must be a number, got {value!r}")
    return float(value)


def _dataset_spec(name: str, raw: Any, base: Path) -> DatasetSpec:
    where = f"datasets.{name}"
    if not IDENT.match(name):
        raise _fail(
            "datasets", f"{name!r} is not a usable name (letters, digits and underscores, not starting with a digit)"
        )
    if isinstance(raw, str):
        raw = {"path": raw}
    if not isinstance(raw, dict) or set(raw) - {"path", "format", "all_varchar"} or "path" not in raw:
        raise _fail(where, "expected a path, or a mapping with path and optionally format and all_varchar")
    path = Path(str(raw["path"]))
    path = path if path.is_absolute() else base / path
    fmt = str(raw.get("format") or "").lower()
    if not fmt:
        fmt = {".parquet": "parquet", ".csv": "csv"}.get(path.suffix.lower(), "")
    if fmt not in ("csv", "parquet"):
        raise _fail(where, f"cannot tell the format of {path.name}: use a .csv or .parquet file, or set format")
    all_varchar = raw.get("all_varchar", False)
    if not isinstance(all_varchar, bool):
        raise _fail(where, "all_varchar must be true or false")
    if all_varchar and fmt != "csv":
        raise _fail(where, "all_varchar applies to CSV files only (a Parquet file has its types already)")
    return DatasetSpec(name, path, fmt, all_varchar)


def _check_params(rule_type: str, raw: dict[str, Any], where: str) -> dict[str, Any]:
    required, optional = SPECS[rule_type]
    allowed = COMMON | required | optional
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise _fail(where, f"unknown key(s) for a {rule_type} rule: {', '.join(unknown)}")
    missing = sorted(required - set(raw))
    if missing:
        raise _fail(where, f"a {rule_type} rule needs: {', '.join(missing)}")
    params = {k: raw[k] for k in (required | optional) if k in raw}
    if rule_type == "unique":
        cols = params["columns"]
        cols = [cols] if isinstance(cols, str) else cols
        if not isinstance(cols, list) or not cols or not all(isinstance(c, str) for c in cols):
            raise _fail(where, "columns must be a column name or a list of names")
        params["columns"] = cols
    if rule_type == "range" and not ({"min", "max"} & set(params)):
        raise _fail(where, "a range rule needs min, max or both")
    if rule_type == "range" and "column" not in params:
        raise _fail(where, "a range rule needs: column")
    if rule_type == "row_count":
        if not ({"min", "max"} & set(params)):
            raise _fail(where, "a row_count rule needs min, max or both")
        for key in ("min", "max"):
            if key in params and (isinstance(params[key], bool) or not isinstance(params[key], int) or params[key] < 0):
                raise _fail(where, f"{key} must be a whole number of rows, 0 or more")
    if rule_type == "allowed_values":
        values = params["values"]
        if not isinstance(values, list) or not values:
            raise _fail(where, "values must be a non-empty list")
        params["values"] = [str(v) for v in values]
    if rule_type == "regex" and (not isinstance(params["pattern"], str) or not params["pattern"]):
        raise _fail(where, "pattern must be a non-empty string")
    if rule_type == "expression" and (not isinstance(params["sql"], str) or not params["sql"].strip()):
        raise _fail(where, "sql must be a non-empty SQL condition")
    if rule_type == "outliers":
        method = str(params.get("method", "iqr"))
        if method not in ("iqr", "zscore"):
            raise _fail(where, "method must be iqr or zscore")
        default = 1.5 if method == "iqr" else 3.0
        threshold = _number(params.get("threshold", default), where, "threshold")
        if threshold <= 0:
            raise _fail(where, "threshold must be positive")
        params.update(method=method, threshold=threshold)
    if rule_type == "freshness":
        days = _number(params["max_age_days"], where, "max_age_days")
        if days < 0:
            raise _fail(where, "max_age_days must be 0 or more")
        params["max_age_days"] = days
    if rule_type == "foreign_key":
        for key in ("ref_dataset", "ref_column"):
            if not isinstance(params[key], str):
                raise _fail(where, f"{key} must be a name")
    for key in ("column",):
        if key in params and not isinstance(params[key], str):
            raise _fail(where, f"{key} must be a column name")
    return params


def parse_rules(raw: Any, base: Path) -> RuleSet:
    """Validate the content of a rules file (already read from YAML); relative paths are resolved against base."""
    if not isinstance(raw, dict) or set(raw) - {"datasets", "rules", "as_of"}:
        raise ConfigError("the rules file must be a mapping with datasets and rules (and optionally as_of)")
    raw_datasets = raw.get("datasets")
    if not isinstance(raw_datasets, dict) or not raw_datasets:
        raise ConfigError("datasets: list at least one dataset, as name: path")
    datasets = {str(n): _dataset_spec(str(n), p, base) for n, p in raw_datasets.items()}
    raw_rules = raw.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise ConfigError("rules: list at least one rule")
    rules: list[Rule] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_rules, start=1):
        if not isinstance(item, dict):
            raise ConfigError(f"rules[{index}]: each rule is a mapping")
        rule_id = str(item.get("id") or f"R{index:02d}")
        where = f"rule {rule_id}"
        if rule_id in seen:
            raise _fail(where, "the id is used twice")
        seen.add(rule_id)
        rule_type = str(item.get("type", ""))
        if rule_type not in SPECS:
            raise _fail(where, f"unknown type {rule_type!r}; the types are {', '.join(SPECS)}")
        dataset = str(item.get("dataset", ""))
        if dataset not in datasets:
            raise _fail(where, f"dataset {dataset!r} is not in datasets ({', '.join(datasets)})")
        severity = str(item.get("severity", "error"))
        if severity not in SEVERITIES:
            raise _fail(where, "severity must be error or warn")
        max_failures = item.get("max_failures", 0)
        if isinstance(max_failures, bool) or not isinstance(max_failures, int) or max_failures < 0:
            raise _fail(where, "max_failures must be a whole number, 0 or more")
        rate = _number(item.get("max_failure_rate", 0.0), where, "max_failure_rate")
        if not 0 <= rate <= 1:
            raise _fail(where, "max_failure_rate must be between 0 and 1")
        params = _check_params(rule_type, item, where)
        if rule_type == "foreign_key" and params["ref_dataset"] not in datasets:
            raise _fail(where, f"ref_dataset {params['ref_dataset']!r} is not in datasets")
        rules.append(
            Rule(rule_id, rule_type, dataset, severity, max_failures, rate, str(item.get("description", "")), params)
        )
    as_of = raw.get("as_of")
    if isinstance(as_of, datetime):
        as_of = as_of.date()
    if isinstance(as_of, str):
        try:
            as_of = date.fromisoformat(as_of)
        except ValueError:
            raise ConfigError("as_of must be a date such as 2026-01-01") from None
    if as_of is not None and not isinstance(as_of, date):
        raise ConfigError("as_of must be a date such as 2026-01-01")
    return RuleSet(datasets, rules, as_of)


def load_rules(path: Path) -> RuleSet:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
    return parse_rules(raw, path.resolve().parent)
