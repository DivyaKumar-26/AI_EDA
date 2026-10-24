"""Compare local and global RC proposal policies on saved held-out episode traces."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .diagnose_rc_policy import _classify_miss, _episode_index, _load_run, reachable_states
from .rc_rl_benchmark import (
    DEFAULT_DATASET,
    HELD_OUT_TASKS,
    load_measured_grid,
)


def compare_local_global(
    local_run_dir: str | Path,
    global_run_dir: str | Path,
    output_dir: str | Path,
    *,
    dataset_path: str | Path = DEFAULT_DATASET,
) -> dict:
    """Compare local v5, global proposals, and both global-baseline traces by task/start."""
    output = Path(output_dir).expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing mode-comparison output: {output}")
    local_benchmark, local_tasks, local_data = _load_run(local_run_dir)
    global_benchmark, global_tasks, global_data = _load_run(global_run_dir)
    if local_tasks.get("held_out_tasks") != global_tasks.get("held_out_tasks"):
        raise ValueError("local and global policy runs have different held-out tasks")
    if local_benchmark.get("dataset_provenance") != global_benchmark.get("dataset_provenance"):
        raise ValueError("local and global policy runs use different measured-data provenance")
    if local_benchmark.get("candidate_grid") != global_benchmark.get("candidate_grid"):
        raise ValueError("local and global policy runs use different candidate grids")
    if local_benchmark["summary"].get("proposal_mode", "local") != "local":
        raise ValueError("first run must use local proposal mode")
    if global_benchmark["summary"].get("proposal_mode") != "global":
        raise ValueError("second run must use global proposal mode")
    budget = int(global_benchmark["summary"]["candidate_budget_per_episode"])
    if budget != int(local_benchmark["summary"]["candidate_budget_per_episode"]):
        raise ValueError("local and global policy runs use different candidate budgets")

    local_rl = _episode_index(local_data["rl_policy"])
    global_rl = _episode_index(global_data["rl_policy"])
    old_baseline = _episode_index(global_data["conventional_analytical_ranking"])
    start_aware = _episode_index(global_data["start_aware_conventional_global_ranking"])
    keys = set(local_rl)
    if keys != set(global_rl) or keys != set(old_baseline) or keys != set(start_aware):
        raise ValueError("run traces do not cover identical task/start pairs")
    measured, _ = load_measured_grid(dataset_path)
    local_action_count = budget - 1
    global_action_count = budget - 1
    all_grid_states = set(measured)
    rows: list[dict[str, Any]] = []
    local_ceiling = 0
    global_ceiling = 0
    categories = {"local_policy": {}, "global_policy": {}}
    for task in HELD_OUT_TASKS:
        low, high = task["cutoff_range_hz"]
        passing = {state for state, value in measured.items() if low <= value <= high}
        for key in sorted(key for key in keys if key[0] == task["task_id"]):
            start = key[1]
            local_reachable = reachable_states(start, local_action_count)
            global_reachable = {start} if global_action_count == 0 else all_grid_states
            local_structural_pass = bool(passing & local_reachable)
            global_structural_pass = bool(passing & global_reachable)
            local_ceiling += int(local_structural_pass)
            global_ceiling += int(global_structural_pass)
            local_class = _classify_miss(local_rl[key], passing, local_reachable)
            global_class = _classify_miss(global_rl[key], passing, global_reachable)
            for label, miss_class in (("local_policy", local_class), ("global_policy", global_class)):
                categories[label][miss_class] = categories[label].get(miss_class, 0) + 1
            rows.append({
                "task_id": task["task_id"],
                "target_range_hz": [low, high],
                "start_state": list(start),
                "passing_grid_states": [list(state) for state in sorted(passing)],
                "local_policy": {
                    "success": local_rl[key]["success"],
                    "miss_class": local_class,
                    "stop_reason": local_rl[key]["stop_reason"],
                    "attempts": local_rl[key]["attempts"],
                },
                "global_policy": {
                    "success": global_rl[key]["success"],
                    "miss_class": global_class,
                    "stop_reason": global_rl[key]["stop_reason"],
                    "attempts": global_rl[key]["attempts"],
                },
                "historical_analytical_baseline": {
                    "success": old_baseline[key]["success"],
                    "attempts": old_baseline[key]["attempts"],
                },
                "start_aware_analytical_baseline": {
                    "success": start_aware[key]["success"],
                    "attempts": start_aware[key]["attempts"],
                },
            })
    total = len(rows)
    if total != 45:
        raise ValueError(f"expected 45 held-out task/start pairs, found {total}")
    local_passes = sum(row["local_policy"]["success"] for row in rows)
    global_passes = sum(row["global_policy"]["success"] for row in rows)
    historical_baseline = global_benchmark["summary"]["conventional_analytical_ranking"]
    start_aware_baseline = global_benchmark["summary"]["start_aware_conventional_global_ranking"]
    result = {
        "comparison_id": "rc_local_vs_global_proposal_v1",
        "local_run": str(Path(local_run_dir)),
        "global_run": str(Path(global_run_dir)),
        "held_out_episodes": total,
        "candidate_measurement_budget": budget,
        "local_adjacent_moves_after_start_measurement": local_action_count,
        "global_direct_proposals_after_start_measurement": global_action_count,
        "local_theoretical_max_passes": local_ceiling,
        "global_theoretical_max_passes": global_ceiling,
        "local_policy": {"measured_passes": local_passes, "measured_pass_rate": local_passes / total,
                         "miss_categories": categories["local_policy"]},
        "global_policy": {"measured_passes": global_passes, "measured_pass_rate": global_passes / total,
                          "miss_categories": categories["global_policy"]},
        "historical_analytical_baseline_without_start_charge": historical_baseline,
        "start_aware_analytical_baseline": start_aware_baseline,
        "candidate_access_note": "All methods use the same measured candidate table. Local RL can only move to adjacent grid states. Global RL and both ranking baselines can select any unvisited grid candidate directly after the start measurement; the historical baseline does not charge that start measurement, while the start-aware baseline does.",
        "per_task_start_traces": rows,
        "new_ltspice_runs": False,
        "production_qualified": False,
    }
    output.mkdir(parents=True, exist_ok=False)
    (output / "comparison.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "report.md").write_text(_render_report(result), encoding="utf-8")
    return result


def _render_report(result: dict) -> str:
    return "\n".join([
        "# Local versus global RC proposals",
        "",
        f"- Local policy: {result['local_policy']['measured_passes']}/45 measured passes.",
        f"- Global policy: {result['global_policy']['measured_passes']}/45 measured passes.",
        f"- Historical analytical ranking (start uncharged): {result['historical_analytical_baseline_without_start_charge']['measured_passes']}/45.",
        f"- Start-aware analytical ranking: {result['start_aware_analytical_baseline']['measured_passes']}/45.",
        f"- Theoretical local/global pass ceilings under budget {result['candidate_measurement_budget']}: {result['local_theoretical_max_passes']}/45 and {result['global_theoretical_max_passes']}/45.",
        "",
        result["candidate_access_note"],
        "",
        "Global RL masks all previously measured candidates during training and evaluation. All acceptance checks use the existing saved measured cutoffs. This is an offline fixed-grid benchmark, not a simulator or production qualification result.",
        "",
        "See comparison.json for task/start traces and miss classifications.",
        "",
    ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare local and global RC policy traces")
    parser.add_argument("--local-run", required=True)
    parser.add_argument("--global-run", required=True)
    parser.add_argument("--output-dir", required=True, help="fresh directory; existing outputs are never overwritten")
    args = parser.parse_args(argv)
    try:
        result = compare_local_global(args.local_run, args.global_run, args.output_dir)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({key: value for key, value in result.items() if key != "per_task_start_traces"}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
