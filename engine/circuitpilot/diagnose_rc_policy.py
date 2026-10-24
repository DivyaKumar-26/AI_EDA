"""Compare saved RC policy runs and quantify their local-move reachability ceiling."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .rc_rl_benchmark import DEFAULT_DATASET, HELD_OUT_TASKS, R_VALUES_OHM, C_VALUES_F, load_measured_grid


def _load_run(run_dir: str | Path) -> tuple[dict, dict, dict]:
    root = Path(run_dir).expanduser().resolve()
    try:
        return tuple(json.loads((root / filename).read_text(encoding="utf-8"))
                     for filename in ("benchmark.json", "tasks.json", "episodes.json"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load benchmark artifacts from {root}: {exc}") from exc


def _local_neighbors(state: tuple[int, int]) -> set[tuple[int, int]]:
    ri, ci = state
    candidates = ((ri + 1, ci), (ri - 1, ci), (ri, ci + 1), (ri, ci - 1))
    return {candidate for candidate in candidates if 0 <= candidate[0] < len(R_VALUES_OHM)
            and 0 <= candidate[1] < len(C_VALUES_F)}


def reachable_states(start: tuple[int, int], action_count: int) -> set[tuple[int, int]]:
    """Grid states reachable using at most action_count adjacent R/C moves."""
    reached = {start}
    frontier = {start}
    for _ in range(action_count):
        frontier = {neighbor for state in frontier for neighbor in _local_neighbors(state)} - reached
        reached.update(frontier)
    return reached


def _episode_index(episodes: list[dict]) -> dict[tuple[str, tuple[int, int]], dict]:
    indexed: dict[tuple[str, tuple[int, int]], dict] = {}
    for episode in episodes:
        key = episode["task_id"], tuple(episode["start_state"])
        if key in indexed:
            raise ValueError(f"duplicate evaluation episode: {key}")
        indexed[key] = episode
    return indexed


def _classify_miss(
    episode: dict, passing_states: set[tuple[int, int]], structurally_reachable: set[tuple[int, int]],
) -> str:
    if episode["success"]:
        return "measured_pass"
    if not passing_states:
        return "no_passing_candidate_on_grid"
    if not (passing_states & structurally_reachable):
        return "structurally_unreachable_within_local_budget"
    visited = [tuple(attempt["state"]) for attempt in episode["attempts"]]
    if len(visited) != len(set(visited)):
        return "avoidable_policy_miss_with_repeated_or_stalled_state"
    return "avoidable_policy_miss_reachable_candidate_not_selected"


def compare_runs(
    prior_run_dir: str | Path,
    new_run_dir: str | Path,
    output_dir: str | Path,
    *,
    dataset_path: str | Path = DEFAULT_DATASET,
) -> dict:
    """Compare held-out traces and derive the local-RL theoretical pass ceiling."""
    output = Path(output_dir).expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing diagnostic output: {output}")
    prior_benchmark, prior_tasks, prior_episode_data = _load_run(prior_run_dir)
    new_benchmark, new_tasks, new_episode_data = _load_run(new_run_dir)
    if prior_tasks.get("held_out_tasks") != new_tasks.get("held_out_tasks"):
        raise ValueError("policy runs do not have identical held-out task definitions")
    if prior_benchmark.get("dataset_provenance") != new_benchmark.get("dataset_provenance"):
        raise ValueError("policy runs do not use the same measured-data provenance")
    if prior_benchmark.get("candidate_grid") != new_benchmark.get("candidate_grid"):
        raise ValueError("policy runs do not use the same measured candidate grid")
    prior_rl = _episode_index(prior_episode_data["rl_policy"])
    new_rl = _episode_index(new_episode_data["rl_policy"])
    prior_conventional = _episode_index(prior_episode_data["conventional_analytical_ranking"])
    new_conventional = _episode_index(new_episode_data["conventional_analytical_ranking"])
    expected_keys = set(prior_rl)
    if expected_keys != set(new_rl) or expected_keys != set(prior_conventional) or expected_keys != set(new_conventional):
        raise ValueError("policy runs do not contain the same held-out task/start pairs for RL and baseline")
    if prior_conventional != new_conventional:
        raise ValueError("conventional episode traces differ; the comparison does not hold the baseline fixed")

    measured, _ = load_measured_grid(dataset_path)
    candidate_budget = int(new_benchmark["summary"]["candidate_budget_per_episode"])
    prior_budget = int(prior_benchmark["summary"]["candidate_budget_per_episode"])
    if candidate_budget != prior_budget:
        raise ValueError("policy runs use different candidate budgets")
    action_count = candidate_budget - 1  # one candidate measurement is used for the start state
    rows: list[dict[str, Any]] = []
    max_reachable = 0
    category_counts = {"prior": {}, "new": {}}
    for task in HELD_OUT_TASKS:
        task_id = task["task_id"]
        low, high = task["cutoff_range_hz"]
        passing = {state for state, value in measured.items() if low <= value <= high}
        for key in sorted((key for key in expected_keys if key[0] == task_id)):
            start = key[1]
            reachable = reachable_states(start, action_count)
            structurally_passes = bool(passing & reachable)
            max_reachable += int(structurally_passes)
            prior_episode, new_episode = prior_rl[key], new_rl[key]
            prior_class = _classify_miss(prior_episode, passing, reachable)
            new_class = _classify_miss(new_episode, passing, reachable)
            category_counts["prior"][prior_class] = category_counts["prior"].get(prior_class, 0) + 1
            category_counts["new"][new_class] = category_counts["new"].get(new_class, 0) + 1
            rows.append({
                "task_id": task_id,
                "target_range_hz": [low, high],
                "start_state": list(start),
                "passing_grid_states": [list(state) for state in sorted(passing)],
                "states_reachable_with_budget": [list(state) for state in sorted(reachable)],
                "structurally_reachable_pass": structurally_passes,
                "prior_v4": {"success": prior_episode["success"], "miss_class": prior_class,
                             "stop_reason": prior_episode["stop_reason"],
                             "attempts": prior_episode["attempts"]},
                "new_v5": {"success": new_episode["success"], "miss_class": new_class,
                           "stop_reason": new_episode["stop_reason"],
                           "attempts": new_episode["attempts"]},
                "conventional_baseline_v4": {
                    "success": prior_conventional[key]["success"],
                    "attempts": prior_conventional[key]["attempts"],
                },
                "conventional_baseline_v5": {
                    "success": new_conventional[key]["success"],
                    "attempts": new_conventional[key]["attempts"],
                },
            })
    total = len(rows)
    if total != 45:
        raise ValueError(f"expected 45 held-out episodes per policy, got {total}")
    max_rate = max_reachable / total if total else 0.0
    prior_passes = sum(row["prior_v4"]["success"] for row in rows)
    new_passes = sum(row["new_v5"]["success"] for row in rows)
    baseline = new_benchmark["summary"]["conventional_analytical_ranking"]
    unreachable_count = category_counts["new"].get("structurally_unreachable_within_local_budget", 0)
    reachable_count = total - unreachable_count
    prior_reachable_outcomes = prior_passes + sum(
        count for category, count in category_counts["prior"].items()
        if category.startswith("avoidable_policy_miss_")
    )
    new_reachable_outcomes = new_passes + sum(
        count for category, count in category_counts["new"].items()
        if category.startswith("avoidable_policy_miss_")
    )
    if prior_reachable_outcomes != max_reachable or new_reachable_outcomes != max_reachable:
        raise ValueError("per-policy pass/miss categories do not reconcile with the structural reachability ceiling")
    comparison = {
        "diagnostic_id": "rc_local_move_policy_comparison_v1",
        "prior_run": str(Path(prior_run_dir)),
        "new_run": str(Path(new_run_dir)),
        "measured_grid_only": True,
        "new_ltspice_runs": False,
        "held_out_episodes": total,
        "candidate_measurement_budget": candidate_budget,
        "rl_local_actions_available_after_start_measurement": action_count,
        "candidate_access_difference": "RL can reach only adjacent R/C grid states within the action budget; analytical ranking can select any of the nine grid states on its first ranked candidate.",
        "theoretical_rl_max_passes_under_local_moves": max_reachable,
        "theoretical_rl_max_pass_rate": max_rate,
        "structurally_unreachable_task_start_pairs": unreachable_count,
        "structurally_reachable_task_start_pairs": reachable_count,
        "prior_v4_reachable_passes_and_misses": {
            "passes": prior_passes,
            "reachable_misses": prior_reachable_outcomes - prior_passes,
        },
        "new_v5_reachable_passes_and_misses": {
            "passes": new_passes,
            "reachable_misses": new_reachable_outcomes - new_passes,
        },
        "prior_v4": {"measured_passes": prior_passes, "measured_pass_rate": prior_passes / total,
                     "miss_categories": category_counts["prior"]},
        "new_v5": {"measured_passes": new_passes, "measured_pass_rate": new_passes / total,
                   "miss_categories": category_counts["new"]},
        "conventional_analytical_ranking": baseline,
        "per_task_start_comparison": rows,
        "policy_status": "experimental_not_integrated",
        "production_qualified": False,
    }
    output.mkdir(parents=True, exist_ok=False)
    (output / "comparison.json").write_text(json.dumps(comparison, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "report.md").write_text(_render_report(comparison), encoding="utf-8")
    return comparison


def _render_report(comparison: dict) -> str:
    budget = comparison["candidate_measurement_budget"]
    return "\n".join([
        "# RC policy training comparison",
        "",
        f"- Prior v4: {comparison['prior_v4']['measured_passes']}/45 passes ({comparison['prior_v4']['measured_pass_rate']:.1%}).",
        f"- New v5: {comparison['new_v5']['measured_passes']}/45 passes ({comparison['new_v5']['measured_pass_rate']:.1%}).",
        f"- Conventional analytical ranking: {comparison['conventional_analytical_ranking']['measured_passes']}/45 passes ({comparison['conventional_analytical_ranking']['measured_pass_rate']:.1%}).",
        f"- Structural RL ceiling at budget {budget}: {comparison['theoretical_rl_max_passes_under_local_moves']}/45 ({comparison['theoretical_rl_max_pass_rate']:.1%}).",
        f"- Reachability accounting: {comparison['structurally_reachable_task_start_pairs']} reachable pairs; v4 passed {comparison['prior_v4_reachable_passes_and_misses']['passes']} and missed {comparison['prior_v4_reachable_passes_and_misses']['reachable_misses']}; v5 passed {comparison['new_v5_reachable_passes_and_misses']['passes']} and missed {comparison['new_v5_reachable_passes_and_misses']['reachable_misses']}; {comparison['structurally_unreachable_task_start_pairs']} pairs are structurally unreachable.",
        "",
        "An equal candidate-measurement budget does not give the methods the same candidate access. RL starts at the supplied grid state and can make only adjacent R/C moves; the budget of three includes the start measurement, leaving two moves. Analytical ranking can jump directly to any grid candidate, so its 45/45 result is not bounded by RL's local-move reachability ceiling.",
        "",
        "The miss-category counts separate tasks with no passing grid point, structurally unreachable passing points under the local budget, repeated/stalled trajectories, and avoidable misses where a passing point was reachable. See `comparison.json` for every task/start trace.",
        "",
        "This is a fixed-grid offline diagnostic using the existing saved LTspice measurements. No new simulations were run; it is not production qualification.",
        "",
    ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnose saved RC policy runs and local-move limits")
    parser.add_argument("--prior-run", required=True)
    parser.add_argument("--new-run", required=True)
    parser.add_argument("--output-dir", required=True, help="fresh output directory; existing outputs are never overwritten")
    args = parser.parse_args(argv)
    try:
        comparison = compare_runs(args.prior_run, args.new_run, args.output_dir)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({key: value for key, value in comparison.items() if key != "per_task_start_comparison"}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
