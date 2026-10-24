import math
import os
from pathlib import Path
import subprocess
import sys
import unittest

from circuitpilot.rc_lowpass import build_rc_lowpass, cutoff_frequency_hz


class RCLowpassTests(unittest.TestCase):
    def test_readme_smoke_values(self):
        circuit = build_rc_lowpass(1_000, 100e-9)
        self.assertAlmostEqual(circuit.cutoff_hz, 1_591.54943, places=4)

    def test_component_change_changes_cutoff_deterministically(self):
        first = build_rc_lowpass(1_000, 100e-9)
        doubled_cap = build_rc_lowpass(1_000, 200e-9)
        self.assertAlmostEqual(doubled_cap.cutoff_hz, first.cutoff_hz / 2)
        self.assertEqual(first.to_spice(), build_rc_lowpass(1_000.0, 1e-7).to_spice())
        self.assertNotEqual(first.to_spice(), doubled_cap.to_spice())

    def test_spice_is_stable_and_contains_expected_connectivity(self):
        rendered = build_rc_lowpass(1_000, 100e-9).to_spice()
        self.assertEqual(rendered, "* CircuitPilot first-order RC low-pass\nV1 in 0 AC 1\nR1 in out 1000\nC1 out 0 1e-07\n.ac dec 100 1 1Meg\n.end\n")

    def test_rejects_nonpositive_nonfinite_and_unparseable_values(self):
        for value in (0, -1, math.inf, -math.inf, math.nan, "bad", None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    cutoff_frequency_hz(value, 1e-7)
        with self.assertRaises(ValueError):
            build_rc_lowpass(1_000, 0)

    def test_rejects_cutoff_outside_representable_float_range(self):
        for resistance, capacitance in ((1e308, 1e308), (1e-308, 1e-308)):
            with self.subTest(resistance=resistance, capacitance=capacitance):
                with self.assertRaisesRegex(ValueError, "outside the representable"):
                    cutoff_frequency_hz(resistance, capacitance)

    def test_cli_success_and_invalid_input(self):
        root = Path(__file__).resolve().parents[2]
        env = os.environ.copy()
        env["PYTHONPATH"] = str(root / "engine")
        good = subprocess.run(
            [sys.executable, "-m", "circuitpilot", "--r", "1000", "--c", "1e-7"],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertIn("1591.55 Hz (not simulation-verified)", good.stdout)
        self.assertIn("R1 in out 1000", good.stdout)

        bad = subprocess.run(
            [sys.executable, "-m", "circuitpilot", "--r", "-1", "--c", "1e-7"],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(bad.returncode, 2)
        self.assertIn("resistance_ohm must be a finite positive number", bad.stderr)

    def test_cli_simulation_requires_existing_executable(self):
        root = Path(__file__).resolve().parents[2]
        env = os.environ.copy()
        env["PYTHONPATH"] = str(root / "engine")
        result = subprocess.run(
            [sys.executable, "-m", "circuitpilot", "--r", "1000", "--c", "1e-7", "--simulate", "--ltspice", str(root / "missing.exe")],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("LTspice executable not found", result.stderr)


if __name__ == "__main__":
    unittest.main()
