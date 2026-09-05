from __future__ import annotations

import argparse
import csv
import json
from itertools import combinations
from pathlib import Path
from statistics import mean
from typing import Any


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: Any) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False, sort_keys=True)
        handle.write("\n")


def levenshtein(a: list[str], b: list[str]) -> int:
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, left in enumerate(a, start=1):
        current = [i]
        for j, right in enumerate(b, start=1):
            insert = current[j - 1] + 1
            delete = previous[j] + 1
            replace = previous[j - 1] + (0 if left == right else 1)
            current.append(min(insert, delete, replace))
        previous = current
    return previous[-1]


def tss(a: list[str], b: list[str]) -> float:
    denominator = max(len(a), len(b))
    if denominator == 0:
        return 1.0
    return 1.0 - levenshtein(a, b) / denominator


def flatten_arguments(value: Any, prefix: str = "") -> set[str]:
    items: set[str] = set()
    if isinstance(value, dict):
        for key in sorted(value):
            child = f"{prefix}.{key}" if prefix else str(key)
            items |= flatten_arguments(value[key], child)
        return items
    if isinstance(value, list):
        for index, child_value in enumerate(value):
            child = f"{prefix}[{index}]"
            items |= flatten_arguments(child_value, child)
        return items
    items.add(f"{prefix}={json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)}")
    return items


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


def argument_consistency(run_a: dict[str, Any], run_b: dict[str, Any]) -> float:
    tools_a = list(run_a.get("tool_sequence") or [])
    tools_b = list(run_b.get("tool_sequence") or [])
    args_a = list(run_a.get("tool_arguments") or [])
    args_b = list(run_b.get("tool_arguments") or [])
    length = max(len(tools_a), len(tools_b))
    if length == 0:
        return 1.0

    scores: list[float] = []
    for index in range(length):
        if index >= len(tools_a) or index >= len(tools_b):
            scores.append(0.0)
            continue
        if tools_a[index] != tools_b[index]:
            scores.append(0.0)
            continue
        left = args_a[index] if index < len(args_a) else {}
        right = args_b[index] if index < len(args_b) else {}
        scores.append(jaccard(flatten_arguments(left), flatten_arguments(right)))
    return mean(scores)


def divergence_point(run_a: dict[str, Any], run_b: dict[str, Any]) -> int | None:
    tools_a = list(run_a.get("tool_sequence") or [])
    tools_b = list(run_b.get("tool_sequence") or [])
    args_a = list(run_a.get("tool_arguments") or [])
    args_b = list(run_b.get("tool_arguments") or [])
    length = max(len(tools_a), len(tools_b))
    for index in range(length):
        if index >= len(tools_a) or index >= len(tools_b):
            return index + 1
        if tools_a[index] != tools_b[index]:
            return index + 1
        left = args_a[index] if index < len(args_a) else {}
        right = args_b[index] if index < len(args_b) else {}
        if flatten_arguments(left) != flatten_arguments(right):
            return index + 1
    return None


def safe_mean(values: list[Any]) -> float | None:
    clean = [float(value) for value in values if value is not None]
    return mean(clean) if clean else None


def pairwise_metrics(runs: list[dict[str, Any]]) -> dict[str, Any]:
    if len(runs) < 2:
        return {
            "tss": None,
            "argument_consistency": None,
            "structured_diagnosis_agreement": None,
            "mean_divergence_point": None,
            "pair_count": 0,
        }

    tss_values: list[float] = []
    ac_values: list[float] = []
    agreement_values: list[float] = []
    divergence_values: list[int] = []

    for left, right in combinations(runs, 2):
        tss_values.append(tss(list(left.get("tool_sequence") or []), list(right.get("tool_sequence") or [])))
        ac_values.append(argument_consistency(left, right))
        agreement_values.append(
            1.0 if left.get("structured_signature") == right.get("structured_signature") else 0.0
        )
        point = divergence_point(left, right)
        if point is not None:
            divergence_values.append(point)

    return {
        "tss": mean(tss_values),
        "argument_consistency": mean(ac_values),
        "structured_diagnosis_agreement": mean(agreement_values),
        "mean_divergence_point": mean(divergence_values) if divergence_values else None,
        "pair_count": len(tss_values),
    }


def summarize_group(profile: str, scenario: str, runs: list[dict[str, Any]]) -> dict[str, Any]:
    reproducibility = pairwise_metrics(runs)
    return {
        "profile": profile,
        "scenario": scenario,
        "runs": len(runs),
        "detection_rate": safe_mean([run.get("detection_rate") for run in runs]),
        "mean_ttd_seconds": safe_mean([run.get("ttd_seconds") for run in runs]),
        "location_accuracy": safe_mean([run.get("location_accuracy") for run in runs]),
        "type_accuracy": safe_mean([run.get("type_accuracy") for run in runs]),
        "evidence_coverage": safe_mean([run.get("evidence_coverage") for run in runs]),
        "diagnostic_score": safe_mean([run.get("diagnostic_score") for run in runs]),
        "mean_react_steps": safe_mean([run.get("react_steps") for run in runs]),
        "mean_tool_calls": safe_mean([run.get("tool_calls") for run in runs]),
        "mean_diagnosis_time_seconds": safe_mean([run.get("diagnosis_time_seconds") for run in runs]),
        "tss": reproducibility["tss"],
        "argument_consistency": reproducibility["argument_consistency"],
        "structured_diagnosis_agreement": reproducibility["structured_diagnosis_agreement"],
        "mean_divergence_point": reproducibility["mean_divergence_point"],
        "pair_count": reproducibility["pair_count"],
    }


def summarize_profile(
    profile: str,
    profile_runs: list[dict[str, Any]],
    scenario_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate correctness over runs and reproducibility only within same-scenario pairs."""

    return {
        "profile": profile,
        "scenario": "overall",
        "runs": len(profile_runs),
        "detection_rate": safe_mean([run.get("detection_rate") for run in profile_runs]),
        "mean_ttd_seconds": safe_mean([run.get("ttd_seconds") for run in profile_runs]),
        "location_accuracy": safe_mean([run.get("location_accuracy") for run in profile_runs]),
        "type_accuracy": safe_mean([run.get("type_accuracy") for run in profile_runs]),
        "evidence_coverage": safe_mean([run.get("evidence_coverage") for run in profile_runs]),
        "diagnostic_score": safe_mean([run.get("diagnostic_score") for run in profile_runs]),
        "mean_react_steps": safe_mean([run.get("react_steps") for run in profile_runs]),
        "mean_tool_calls": safe_mean([run.get("tool_calls") for run in profile_runs]),
        "mean_diagnosis_time_seconds": safe_mean([run.get("diagnosis_time_seconds") for run in profile_runs]),
        "tss": safe_mean([row.get("tss") for row in scenario_rows]),
        "argument_consistency": safe_mean([row.get("argument_consistency") for row in scenario_rows]),
        "structured_diagnosis_agreement": safe_mean([row.get("structured_diagnosis_agreement") for row in scenario_rows]),
        "mean_divergence_point": safe_mean([row.get("mean_divergence_point") for row in scenario_rows]),
        "pair_count": sum(int(row.get("pair_count") or 0) for row in scenario_rows),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-root", required=True)
    args = parser.parse_args()

    results_root = Path(args.results_root).resolve()
    score_files = sorted(results_root.rglob("scores.json"))
    runs = [load_json(path) for path in score_files]

    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for run in runs:
        profile = str(run.get("profile") or "unknown")
        scenario = str(run.get("scenario") or "unknown")
        groups.setdefault((profile, scenario), []).append(run)

    scenario_rows = [
        summarize_group(profile, scenario, group_runs)
        for (profile, scenario), group_runs in sorted(groups.items())
    ]

    by_profile: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        by_profile.setdefault(str(run.get("profile") or "unknown"), []).append(run)

    profile_rows: list[dict[str, Any]] = []
    for profile, profile_runs in sorted(by_profile.items()):
        matching_scenario_rows = [row for row in scenario_rows if row["profile"] == profile]
        profile_rows.append(summarize_profile(profile, profile_runs, matching_scenario_rows))

    summary_rows = [*scenario_rows, *profile_rows]
    summary = {
        "run_count": len(runs),
        "groups": scenario_rows,
        "profiles": profile_rows,
    }
    write_json(results_root / "summary.json", summary)
    write_csv(results_root / "summary.csv", summary_rows)
    write_csv(results_root / "model-comparison.csv", profile_rows)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
