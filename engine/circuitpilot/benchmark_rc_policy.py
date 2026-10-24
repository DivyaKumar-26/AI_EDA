"""Run the offline held-out benchmark for experimental target-aware RC policy learning."""

from __future__ import annotations

import argparse
import json
import sys

from .rc_rl_benchmark import DEFAULT_DATASET, run_benchmark


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark an experimental RC Q policy against analytical ranking")
    parser.add_argument("--output-dir", required=True,
                        help="fresh directory for benchmark policy, tasks, episode traces, and summary (required)")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET),
                        help="benchmark.json from the complete provenance-pinned evidence tree, including all referenced reports/raw files (default is source-checkout-relative and unavailable in the wheel)")
    parser.add_argument("--seed", type=int, default=20261025)
    parser.add_argument("--training-episodes", type=int, default=12000)
    parser.add_argument("--candidate-budget", type=int, default=3,
                        help="maximum measured-grid candidate evaluations per held-out episode")
    parser.add_argument("--training-max-steps", type=int, default=8)
    parser.add_argument("--proposal-mode", choices=("local", "global"), default="local",
                        help="local preserves the legacy adjacent-move policy; global proposes any unvisited grid candidate")
    args = parser.parse_args(argv)
    try:
        result = run_benchmark(
            args.output_dir,
            dataset_path=args.dataset,
            seed=args.seed,
            training_episodes=args.training_episodes,
            candidate_budget=args.candidate_budget,
            training_max_steps=args.training_max_steps,
            proposal_mode=args.proposal_mode,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    json.dump(result["summary"], sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
