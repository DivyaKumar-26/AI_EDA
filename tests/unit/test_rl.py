import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from circuitpilot.rl import (
    RCState,
    RCLowpassTuningEnv,
    evaluate_greedy_policy,
    evaluate_random_policy,
    train_q_learning,
)
from circuitpilot.train_rc_policy import train


class RLEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.r_values = (1.0, 2.0, 3.0)
        self.c_values = (1.0, 2.0, 3.0)
        self.measured = {RCState(i, j): 1.0 / (r * c)
                         for i, r in enumerate(self.r_values)
                         for j, c in enumerate(self.c_values)}

    def test_step_reward_uses_measured_result_and_reports_tolerance(self):
        env = RCLowpassTuningEnv(self.r_values, self.c_values, self.measured, 0.25, seed=1)
        env.reset(RCState(1, 0))
        state, reward, done, info = env.step(2)  # C index 0 -> 1
        self.assertEqual(state, RCState(1, 1))
        self.assertAlmostEqual(info["measured_cutoff_hz"], 0.25)
        self.assertTrue(done)
        self.assertTrue(info["success"])
        self.assertGreater(reward, 0)

    def test_seeded_q_learning_is_reproducible_and_beats_random_on_steps(self):
        env = RCLowpassTuningEnv(self.r_values, self.c_values, self.measured, 0.25, max_steps=8, seed=23)
        first = train_q_learning(env, episodes=3000, seed=23)
        first_eval = evaluate_greedy_policy(env, first)
        second = train_q_learning(env, episodes=3000, seed=23)
        second_eval = evaluate_greedy_policy(env, second)
        self.assertEqual(first, second)
        self.assertEqual(first_eval["successes"], 9)
        random_eval = evaluate_random_policy(env, repetitions=50, seed=24)
        self.assertLess(first_eval["mean_steps"], random_eval["mean_steps"])

    def test_invalid_boundary_action_stays_in_grid(self):
        env = RCLowpassTuningEnv(self.r_values, self.c_values, self.measured, 0.25)
        env.reset(RCState(0, 0))
        state, reward, _, info = env.step(1)
        self.assertEqual(state, RCState(0, 0))
        self.assertLess(reward, 0)
        self.assertEqual(info["action"], "R_DOWN")

    def test_training_refuses_existing_final_outputs_before_simulation(self):
        for existing_name in ("checkpoint.json", "benchmark.json"):
            with self.subTest(existing_name=existing_name), TemporaryDirectory(dir=Path.cwd()) as temp_dir:
                root = Path(temp_dir)
                existing = root / existing_name
                original = b"preserve this user data\n"
                existing.write_bytes(original)
                with patch("circuitpilot.train_rc_policy.run_ltspice") as run_ltspice:
                    with self.assertRaisesRegex(FileExistsError, "refusing to overwrite"):
                        train(root)
                run_ltspice.assert_not_called()
                self.assertEqual(existing.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
