"""Simulate a bounded RC candidate grid and train/evaluate a tabular Q policy."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from .designs import TOPOLOGY_REGISTRY
from .rc_lowpass import build_rc_lowpass
from .rl import RCState, RCLowpassTuningEnv, evaluate_greedy_policy, evaluate_random_policy, train_q_learning
from .simulation import run_ltspice


R_GRID = (680.0, 1_000.0, 1_500.0)
C_GRID = (47e-9, 100e-9, 220e-9)
DEFAULT_TARGET_HZ = 1.0 / (2.0 * math.pi * 1_000.0 * 100e-9)


def train(output_root: str | Path, executable: str | None = None, target_hz: float = DEFAULT_TARGET_HZ,
          seed: int = 20261024, episodes: int = 3000, timeout_seconds: float = 240.0) -> tuple[Path, Path]:
    """Generate measured rewards, train a Q-table, and save checkpoint and benchmark."""
    root = Path(output_root).expanduser().resolve()
    checkpoint_path = root / "checkpoint.json"
    benchmark_path = root / "benchmark.json"
    existing_outputs = [path.name for path in (checkpoint_path, benchmark_path) if path.exists()]
    if existing_outputs:
        raise FileExistsError(
            f"refusing to overwrite existing training output(s) in {root}: "
            + ", ".join(existing_outputs)
        )
    root.mkdir(parents=True, exist_ok=True)
    measured: dict[RCState, float] = {}
    measurement_rows = []
    for ri, resistance in enumerate(R_GRID):
        for ci, capacitance in enumerate(C_GRID):
            circuit = build_rc_lowpass(resistance, capacitance)
            ir = TOPOLOGY_REGISTRY.synthesize("rc_lowpass", {"resistance_ohm": resistance, "capacitance_f": capacitance})
            if ir.render() != circuit.to_spice():
                raise RuntimeError("RC topology registry and RC generator rendered different candidate circuits")
            label = f"r{int(resistance)}_c{int(round(capacitance * 1e9))}n"
            result = run_ltspice(circuit, root / "simulations" / label, executable, timeout_seconds)
            state = RCState(ri, ci)
            measured[state] = result.simulated_cutoff_hz
            measurement_rows.append({"state": [ri, ci], "resistance_ohm": resistance, "capacitance_f": capacitance,
                                     "simulated_cutoff_hz": result.simulated_cutoff_hz,
                                     "analytical_cutoff_hz": result.analytical_cutoff_hz,
                                     "simulation_report": str(result.report_path.relative_to(root))})

    env = RCLowpassTuningEnv(R_GRID, C_GRID, measured, target_hz, tolerance_percent=1.0, max_steps=8, seed=seed)
    q = train_q_learning(env, episodes=episodes, seed=seed)
    learned = evaluate_greedy_policy(env, q)
    random_baseline = evaluate_random_policy(env, repetitions=50, seed=seed + 1)
    best_state = min(measured, key=lambda state: abs(math.log(measured[state] / target_hz)))
    dataset_json = json.dumps(measurement_rows, sort_keys=True, separators=(",", ":"))
    dataset_hash = hashlib.sha256(dataset_json.encode("utf-8")).hexdigest()
    checkpoint = {
        "format": "circuitpilot-tabular-q-v1",
        "algorithm": "epsilon-greedy tabular Q-learning",
        "seed": seed,
        "episodes": episodes,
        "target_cutoff_hz": target_hz,
        "tolerance_percent": 1.0,
        "actions": list(env.ACTIONS),
        "reward": "log-relative error improvement - 0.02 per measured-table action; +1.0 on target within 1%",
        "resistance_values_ohm": list(R_GRID),
        "capacitance_values_f": list(C_GRID),
        "measured_cutoffs_hz": {f"{state.resistance_index},{state.capacitance_index}": value for state, value in measured.items()},
        "q_values": {f"{state.resistance_index},{state.capacitance_index}": values for state, values in q.items()},
        "greedy_action": {f"{state.resistance_index},{state.capacitance_index}": env.ACTIONS[max(range(len(values)), key=values.__getitem__)]
                          for state, values in q.items()},
        "measurement_dataset_sha256": dataset_hash,
    }
    checkpoint_path.write_text(json.dumps(checkpoint, indent=2) + "\n", encoding="utf-8")
    oracle_error = 100.0 * abs(measured[best_state] - target_hz) / target_hz
    benchmark = {
        "benchmark_id": "rc_lowpass_fixed_target_grid_v1",
        "boundary": "One fixed target and a 3x3 discrete R/C grid; no claim of generalization to other topologies or target ranges.",
        "simulator": "LTspice ASCII batch AC output",
        "simulator_calls_for_measurement_dataset": len(measured),
        "target_cutoff_hz": target_hz,
        "tolerance_percent": 1.0,
        "seed": seed,
        "episodes": episodes,
        "measurement_dataset_sha256": dataset_hash,
        "measured_candidates": measurement_rows,
        "learned_greedy_policy_all_start_states": learned,
        "random_policy_same_starts_50_repeats": random_baseline,
        "grid_oracle_best_state": [best_state.resistance_index, best_state.capacitance_index],
        "grid_oracle_error_percent": oracle_error,
        "interpretation": "The policy is compared with a random walk on the same finite measured grid. The exhaustive grid oracle is an upper bound and is not an RL policy.",
    }
    benchmark_path.write_text(json.dumps(benchmark, indent=2) + "\n", encoding="utf-8")
    return checkpoint_path, benchmark_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Train a bounded RC cutoff tuning policy from LTspice measurements")
    parser.add_argument("--output-root", default="output/rl_rc_v1")
    parser.add_argument("--ltspice", help="path to LTspice.exe; auto-discovered when omitted")
    parser.add_argument("--target-hz", type=float, default=DEFAULT_TARGET_HZ)
    parser.add_argument("--seed", type=int, default=20261024)
    parser.add_argument("--episodes", type=int, default=3000)
    parser.add_argument("--timeout", type=float, default=240.0)
    args = parser.parse_args()
    checkpoint, benchmark = train(args.output_root, args.ltspice, args.target_hz, args.seed, args.episodes, args.timeout)
    print(f"Checkpoint: {checkpoint}")
    print(f"Benchmark: {benchmark}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
