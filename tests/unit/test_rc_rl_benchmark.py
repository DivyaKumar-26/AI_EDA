from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import random
import shutil
from tempfile import TemporaryDirectory
import unittest

from circuitpilot.rc_rl_benchmark import (
    ACTIONS,
    DEFAULT_DATASET,
    GLOBAL_ACTIONS,
    HELD_OUT_TASKS,
    TRAINING_GUARD_FRACTION,
    _available_global_actions,
    _candidate_mask,
    _global_action_candidate,
    _train_global_q_episode,
    generate_training_tasks,
    load_measured_grid,
    run_benchmark,
)
from circuitpilot.benchmark_rc_policy import main as benchmark_cli_main
from circuitpilot.compare_rc_policy_modes import compare_local_global
from circuitpilot.diagnose_rc_policy import compare_runs, reachable_states


class TargetAwareRCPolicyBenchmarkTests(unittest.TestCase):
    def test_global_training_start_pass_is_terminal_without_actions_or_q_updates(self):
        measured, _ = load_measured_grid(DEFAULT_DATASET)
        start = (1, 1)
        task = {
            "task_id": "start-already-passes",
            "target_hz": measured[start],
            "tolerance_percent": 1.0,
        }
        q = {}
        _train_global_q_episode(
            task, start, measured, q, random.Random(5), epsilon=1.0, max_steps=3,
        )
        self.assertEqual(q, {})

    def test_cli_requires_fresh_output_dir_and_runs_when_specified(self):
        error_output = io.StringIO()
        with redirect_stderr(error_output), self.assertRaises(SystemExit) as raised:
            benchmark_cli_main([])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("the following arguments are required: --output-dir", error_output.getvalue())

        with TemporaryDirectory(dir=Path.cwd()) as root:
            output = Path(root) / "fresh-run"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                status = benchmark_cli_main([
                    "--output-dir", str(output), "--seed", "73",
                    "--training-episodes", "100", "--candidate-budget", "2",
                    "--training-max-steps", "3",
                ])
            self.assertEqual(status, 0)
            self.assertTrue((output / "benchmark.json").is_file())
            self.assertIn('"candidate_budget_per_episode": 2', stdout.getvalue())

    def test_tracked_dataset_is_exact_nine_point_ltspice_evidence(self):
        measured, provenance = load_measured_grid(DEFAULT_DATASET)
        self.assertEqual(len(measured), 9)
        self.assertTrue(provenance["raw_measurements_validated"])
        self.assertTrue(provenance["raw_trace_hashes_pinned"])
        self.assertEqual(len(provenance["trusted_benchmark_content_sha256"]), 64)
        self.assertEqual(provenance["candidate_count"], 9)
        self.assertEqual(provenance["simulator"], "LTspice ASCII batch AC output")

    def test_heldout_target_ranges_are_disjoint_from_training_targets(self):
        measured, _ = load_measured_grid(DEFAULT_DATASET)
        first = generate_training_tasks(measured, 1000, seed=20261025)
        second = generate_training_tasks(measured, 1000, seed=20261025)
        self.assertEqual(first, second)
        self.assertGreater(len({task["target_hz"] for task in first}), 900)
        for task in HELD_OUT_TASKS:
            low, high = task["cutoff_range_hz"]
            guarded_low = low / (1 + TRAINING_GUARD_FRACTION)
            guarded_high = high * (1 + TRAINING_GUARD_FRACTION)
            with self.subTest(task=task["task_id"]):
                self.assertTrue(all(not guarded_low <= train["target_hz"] <= guarded_high for train in first))

    def test_benchmark_is_seed_reproducible_and_preserves_task_policy_and_episode_artifacts(self):
        with TemporaryDirectory(dir=Path.cwd()) as root:
            first = run_benchmark(Path(root) / "first", seed=73, training_episodes=500,
                                  candidate_budget=2, training_max_steps=5)
            second = run_benchmark(Path(root) / "second", seed=73, training_episodes=500,
                                   candidate_budget=2, training_max_steps=5, proposal_mode="local")
            self.assertEqual(first, second)
            first_dir = Path(root) / "first"
            for name in ("training-config.json", "tasks.json", "policy.json", "episodes.json", "summary.json"):
                self.assertTrue((first_dir / name).is_file())
            tasks = json.loads((first_dir / "tasks.json").read_text(encoding="utf-8"))
            self.assertEqual(len(tasks["training_tasks"]), 500)
            self.assertEqual(len(tasks["held_out_tasks"]), len(HELD_OUT_TASKS))
            self.assertTrue(tasks["held_out_targets_disjoint_from_training"])
            self.assertEqual(tasks["training_target_guard_fraction"], TRAINING_GUARD_FRACTION)
            self.assertEqual(len(tasks["training_task_manifest_sha256"]), 64)
            manifest_digest = hashlib.sha256(
                json.dumps(tasks["training_tasks"], sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            self.assertEqual(tasks["training_task_manifest_sha256"], manifest_digest)
            config = json.loads((first_dir / "training-config.json").read_text(encoding="utf-8"))
            self.assertEqual(config["seed"], 73)
            self.assertEqual(config["training_target_guard_fraction"], TRAINING_GUARD_FRACTION)
            self.assertEqual(config["training_episodes"], len(tasks["training_tasks"]))
            episodes = json.loads((first_dir / "episodes.json").read_text(encoding="utf-8"))
            self.assertEqual(len(episodes["rl_policy"]), 45)
            self.assertEqual(len(episodes["conventional_analytical_ranking"]), 45)
            for episode_set in episodes.values():
                for episode in episode_set:
                    self.assertLessEqual(episode["budget_used"], 2)
                    self.assertLessEqual(len(episode["attempts"]), 2)
                    self.assertIn("stop_reason", episode)
                    self.assertTrue(all("measured_cutoff_hz" in attempt for attempt in episode["attempts"]))
            summary = json.loads((first_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["rl"]["episodes"], 45)
            self.assertEqual(summary["conventional_analytical_ranking"]["episodes"], 45)
            self.assertEqual(summary["candidate_budget_per_episode"], 2)
            self.assertFalse(summary["production_qualified"])

    def test_global_proposals_jump_directly_mask_visited_candidates_and_reproduce(self):
        start = (0, 0)
        target_action = GLOBAL_ACTIONS.index("PROPOSE_R2_C2")
        self.assertEqual(_global_action_candidate(target_action), (2, 2))
        self.assertNotIn((2, 2), {(1, 0), (0, 1), start})
        visited_mask = _candidate_mask(start) | _candidate_mask((2, 2))
        available = _available_global_actions(visited_mask)
        self.assertNotIn(0, available)
        self.assertNotIn(target_action, available)
        self.assertEqual(len(available), len(GLOBAL_ACTIONS) - 2)
        self.assertEqual(len(ACTIONS), 4)  # local proposal vocabulary remains unchanged

        with TemporaryDirectory(dir=Path.cwd()) as root:
            first = run_benchmark(Path(root) / "global-a", seed=73, training_episodes=300,
                                  candidate_budget=3, training_max_steps=8, proposal_mode="global")
            second = run_benchmark(Path(root) / "global-b", seed=73, training_episodes=300,
                                   candidate_budget=3, training_max_steps=8, proposal_mode="global")
            self.assertEqual(first, second)
            output = Path(root) / "global-a"
            config = json.loads((output / "training-config.json").read_text(encoding="utf-8"))
            self.assertEqual(config["proposal_mode"], "global")
            self.assertEqual(config["actions"], list(GLOBAL_ACTIONS))
            history = json.loads((output / "episodes.json").read_text(encoding="utf-8"))
            for episode in history["rl_policy"]:
                states = [tuple(attempt["state"]) for attempt in episode["attempts"]]
                self.assertEqual(len(states), len(set(states)))
                self.assertLessEqual(len(states), 3)
                self.assertEqual(episode["proposal_mode"], "global")
            self.assertEqual(len(history["start_aware_conventional_global_ranking"]), 45)
            local = Path(root) / "local-v5"
            run_benchmark(local, seed=73, training_episodes=300, candidate_budget=3, training_max_steps=8,
                          proposal_mode="local")
            comparison = compare_local_global(local, output, Path(root) / "mode-comparison")
            self.assertEqual(comparison["local_theoretical_max_passes"], 33)
            self.assertEqual(comparison["global_theoretical_max_passes"], 45)
            self.assertEqual(len(comparison["per_task_start_traces"]), 45)

    def test_evaluation_budget_is_independent_of_training_step_limit(self):
        with TemporaryDirectory(dir=Path.cwd()) as root:
            run_benchmark(Path(root) / "short-training", seed=73, training_episodes=500,
                          candidate_budget=3, training_max_steps=1)
            episodes = json.loads((Path(root) / "short-training" / "episodes.json").read_text(encoding="utf-8"))
            for episode_set in episodes.values():
                self.assertTrue(all(episode["budget_used"] <= 3 for episode in episode_set))
                self.assertTrue(all(len(episode["attempts"]) <= 3 for episode in episode_set))
            self.assertTrue(any(episode["budget_used"] > 1 for episode in episodes["rl_policy"]))
            self.assertTrue(any(episode["budget_used"] == 3 for episode in episodes["rl_policy"]))

    def test_local_reachability_ceiling_and_saved_comparison_diagnostics(self):
        measured, _ = load_measured_grid(DEFAULT_DATASET)
        budget = 3
        max_pass_pairs = 0
        for task in HELD_OUT_TASKS:
            low, high = task["cutoff_range_hz"]
            passing_states = {state for state, cutoff in measured.items() if low <= cutoff <= high}
            for start in measured:
                reachable = reachable_states(start, budget - 1)
                max_pass_pairs += int(bool(passing_states & reachable))
        self.assertEqual(max_pass_pairs, 33)

        with TemporaryDirectory(dir=Path.cwd()) as root:
            prior = Path(root) / "prior"
            new = Path(root) / "new"
            diagnostics = Path(root) / "diagnostics"
            run_benchmark(prior, seed=31, training_episodes=100, candidate_budget=budget)
            run_benchmark(new, seed=32, training_episodes=100, candidate_budget=budget)
            comparison = compare_runs(prior, new, diagnostics)
            self.assertEqual(comparison["theoretical_rl_max_passes_under_local_moves"], 33)
            self.assertEqual(comparison["structurally_reachable_task_start_pairs"], 33)
            self.assertEqual(
                comparison["new_v5_reachable_passes_and_misses"]["passes"]
                + comparison["new_v5_reachable_passes_and_misses"]["reachable_misses"],
                33,
            )
            self.assertEqual(comparison["held_out_episodes"], 45)
            self.assertIn("analytical ranking can select any", comparison["candidate_access_difference"])
            self.assertTrue((diagnostics / "comparison.json").is_file())
            self.assertIn("equal candidate-measurement budget does not give", (diagnostics / "report.md").read_text())

    def test_dataset_hash_and_missing_simulation_report_fail_closed(self):
        original = json.loads(DEFAULT_DATASET.read_text(encoding="utf-8"))
        with TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            root = Path(temp_dir)
            bad_hash = json.loads(json.dumps(original))
            bad_hash["measurement_dataset_sha256"] = "0" * 64
            bad_hash_path = root / "bad-hash.json"
            bad_hash_path.write_text(json.dumps(bad_hash), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "trusted pinned SHA-256"):
                load_measured_grid(bad_hash_path)

            missing_reports_tree = root / "missing-reports"
            shutil.copytree(DEFAULT_DATASET.parent, missing_reports_tree)
            absent_reports_path = missing_reports_tree / "benchmark.json"
            absent_report = missing_reports_tree / original["measured_candidates"][0]["simulation_report"].replace("\\", "/")
            absent_report.unlink()
            with self.assertRaisesRegex(ValueError, "evidence is unavailable"):
                load_measured_grid(absent_reports_path)

            copied_tree = root / "empty-raw"
            shutil.copytree(DEFAULT_DATASET.parent, copied_tree)
            dataset = copied_tree / "benchmark.json"
            benchmark = json.loads(dataset.read_text(encoding="utf-8"))
            report = copied_tree / benchmark["measured_candidates"][0]["simulation_report"].replace("\\", "/")
            (report.parent / "rc_lowpass.raw").write_bytes(b"")
            with self.assertRaisesRegex(ValueError, "pinned LTspice raw data is unavailable or invalid"):
                load_measured_grid(dataset)

    def test_coordinated_dataset_report_and_empty_raw_tampering_fails_pinned_provenance(self):
        with TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            copied_tree = Path(temp_dir) / "rl_rc_v1"
            shutil.copytree(DEFAULT_DATASET.parent, copied_tree)
            dataset = copied_tree / "benchmark.json"
            benchmark = json.loads(dataset.read_text(encoding="utf-8"))
            row = benchmark["measured_candidates"][0]
            row["simulated_cutoff_hz"] *= 1.01
            canonical = json.dumps(benchmark["measured_candidates"], sort_keys=True, separators=(",", ":"))
            benchmark["measurement_dataset_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            dataset.write_text(json.dumps(benchmark), encoding="utf-8")
            report = copied_tree / row["simulation_report"].replace("\\", "/")
            report_data = json.loads(report.read_text(encoding="utf-8"))
            report_data["measurements"]["simulated_cutoff_hz"] = row["simulated_cutoff_hz"]
            report.write_text(json.dumps(report_data), encoding="utf-8")
            (report.parent / "rc_lowpass.raw").write_bytes(b"")
            with self.assertRaisesRegex(ValueError, "trusted pinned SHA-256"):
                load_measured_grid(dataset)

    def test_output_and_budget_validation_fail_closed_without_overwriting(self):
        with TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            existing = Path(temp_dir) / "existing"
            existing.mkdir()
            marker = existing / "preserve.json"
            marker.write_text('{"keep": true}', encoding="utf-8")
            with self.assertRaisesRegex(FileExistsError, "refusing to overwrite"):
                run_benchmark(existing, training_episodes=10)
            self.assertEqual(marker.read_text(encoding="utf-8"), '{"keep": true}')
            with self.assertRaisesRegex(ValueError, "candidate_budget"):
                run_benchmark(Path(temp_dir) / "invalid", candidate_budget=0)


if __name__ == "__main__":
    unittest.main()
