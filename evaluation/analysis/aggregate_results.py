from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from statistics import mean
from typing import Any


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def write_json(path: Path, value: Any) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False, sort_keys=True)
        handle.write("\n")


def safe_mean(values: list[Any]) -> float | None:
    clean = [float(value) for value in values if value is not None]
    return mean(clean) if clean else None


def summarize_runs(profile: str, scenario: str, runs: list[dict[str, Any]]) -> dict[str, Any]:
    completed = sum(
        1
        for run in runs
        if str(run.get("run_outcome") or "").upper()
        in {"COMPLETED", "RESOLVED", "CLOSED"}
    )
    location_correct = sum(1 for run in runs if bool(run.get("location_correct", False)))
    fault_correct = sum(1 for run in runs if bool(run.get("fault_type_correct", False)))
    evidence_matched = sum(int(run.get("evidence_matched") or 0) for run in runs)
    evidence_expected = sum(int(run.get("evidence_expected") or 0) for run in runs)

    return {
        "profile": profile,
        "scenario": scenario,
        "runs": len(runs),
        "completed_runs": completed,
        "completion_rate": completed / len(runs) if runs else None,
        "correct_location_runs": location_correct,
        "location_correct_rate": location_correct / len(runs) if runs else None,
        "correct_fault_runs": fault_correct,
        "fault_type_correct_rate": fault_correct / len(runs) if runs else None,
        "evidence_points_matched": evidence_matched,
        "evidence_points_expected": evidence_expected,
        "evidence_rate": (
            evidence_matched / evidence_expected if evidence_expected > 0 else None
        ),
        "mean_diagnosis_time_seconds": safe_mean(
            [run.get("diagnosis_time_seconds") for run in runs]
        ),
        "mean_react_steps": safe_mean([run.get("react_steps") for run in runs]),
        "mean_tool_calls": safe_mean([run.get("tool_calls") for run in runs]),
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
        summarize_runs(profile, scenario, group_runs)
        for (profile, scenario), group_runs in sorted(groups.items())
    ]

    by_profile: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        by_profile.setdefault(str(run.get("profile") or "unknown"), []).append(run)

    profile_rows = [
        summarize_runs(profile, "overall", profile_runs)
        for profile, profile_runs in sorted(by_profile.items())
    ]

    summary = {
        "evaluation_scope": "agentic_diagnosis_only",
        "method": "simple_task_oriented_metrics",
        "run_count": len(runs),
        "groups": scenario_rows,
        "profiles": profile_rows,
    }

    write_json(results_root / "summary.json", summary)
    write_csv(results_root / "summary.csv", scenario_rows)
    write_csv(results_root / "model-comparison.csv", profile_rows)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
