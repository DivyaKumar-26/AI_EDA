import json
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from circuitpilot.rc_lowpass import build_rc_lowpass
from circuitpilot.simulation import (
    ACPoint,
    SimulationError,
    discover_ltspice,
    measure_cutoff_hz,
    parse_ascii_ac_raw,
    run_ltspice,
)


def _raw_text(
    transfer_magnitudes: tuple[float, ...] = (1.0, 0.8, 0.5),
    input_magnitudes: tuple[float, ...] | None = None,
) -> str:
    if input_magnitudes is None:
        input_magnitudes = (1.0,) * len(transfer_magnitudes)
    lines = [
        "Title: test",
        "Plotname: AC Analysis",
        "Flags: complex forward log",
        "No. Variables: 3",
        f"No. Points: {len(transfer_magnitudes)}",
        "Variables:",
        "\t0\tfrequency\tfrequency",
        "\t1\tV(in)\tvoltage",
        "\t2\tV(out)\tvoltage",
        "Values:",
    ]
    for index, (transfer_magnitude, input_magnitude) in enumerate(zip(transfer_magnitudes, input_magnitudes)):
        frequency = 10.0 ** index
        output_magnitude = transfer_magnitude * input_magnitude
        lines.extend((f"{index}\t{frequency:.12g},0", f"\t{input_magnitude:.12g},0", f"\t{output_magnitude:.12g},0"))
    return "\n".join(lines) + "\n"


class SimulationTests(unittest.TestCase):
    def test_parse_ascii_ac_raw_output_voltage(self):
        points = parse_ascii_ac_raw(_raw_text())
        self.assertEqual([p.frequency_hz for p in points], [1.0, 10.0, 100.0])
        self.assertEqual([p.input_magnitude for p in points], [1.0, 1.0, 1.0])
        self.assertEqual([p.output_magnitude for p in points], [1.0, 0.8, 0.5])

    def test_cutoff_uses_log_frequency_interpolation(self):
        points = [
            ACPoint(100.0, 1.0, 1.0),
            ACPoint(1_000.0, 1.0, 10 ** (-2.0 / 20.0)),
            ACPoint(10_000.0, 1.0, 10 ** (-4.0 / 20.0)),
        ]
        expected_fraction = (-3.01029995664 - (-2.0)) / (-4.0 - (-2.0))
        expected = 1_000.0 * (10.0 ** expected_fraction)
        self.assertAlmostEqual(measure_cutoff_hz(points), expected, places=8)

    def test_varying_input_amplitude_uses_vout_over_vin(self):
        raw = _raw_text((1.0, 0.8, 0.5), (2.0, 0.25, 4.0))
        points = parse_ascii_ac_raw(raw)
        self.assertEqual([point.input_magnitude for point in points], [2.0, 0.25, 4.0])
        expected_fraction = (-3.01029995664 - 20.0 * math.log10(0.8)) / (
            20.0 * math.log10(0.5) - 20.0 * math.log10(0.8)
        )
        expected = 10.0 * (10.0 ** expected_fraction)
        self.assertAlmostEqual(measure_cutoff_hz(points), expected, places=8)

    def test_low_edge_cutoff_is_reported_out_of_sweep_instead_of_false_crossing(self):
        # For a true 0.8 Hz RC cutoff, |H(1 Hz)| ~= 0.625. Normalizing to
        # that first point invents a later ~1.62 Hz crossing.
        low_edge_transfer = 1.0 / (1.0 + (1.0 / 0.8) ** 2) ** 0.5
        points = [
            ACPoint(1.0, 1.0, low_edge_transfer),
            ACPoint(1.1, 2.0, 2.0 / (1.0 + (1.1 / 0.8) ** 2) ** 0.5),
            ACPoint(1.2, 0.5, 0.5 / (1.0 + (1.2 / 0.8) ** 2) ** 0.5),
        ]
        with self.assertRaisesRegex(SimulationError, "below -3.0103 dB"):
            measure_cutoff_hz(points)

    def test_cutoff_rejects_missing_crossing(self):
        with self.assertRaisesRegex(SimulationError, "did not contain"):
            measure_cutoff_hz([ACPoint(1.0, 1.0, 1.0), ACPoint(10.0, 1.0, 0.9)])

    def test_discovery_uses_home_install_when_localappdata_and_path_are_empty(self):
        project_root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(dir=project_root) as temporary:
            fake_home = Path(temporary)
            install = fake_home / "AppData" / "Local" / "Programs" / "ADI" / "LTspice" / "LTspice.exe"
            install.parent.mkdir(parents=True)
            install.write_text("test executable")
            with patch("circuitpilot.simulation.Path.home", return_value=fake_home), patch(
                "circuitpilot.simulation.shutil.which", return_value=None
            ), patch.dict(os.environ, {"LOCALAPPDATA": ""}):
                self.assertEqual(discover_ltspice(), install.resolve())

    def test_runner_invokes_ltspice_and_writes_simulation_report(self):
        project_root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(dir=project_root) as temporary:
            root = Path(temporary)
            exe = root / "LTspice.exe"
            exe.write_text("test executable")
            output_dir = root / "run"

            def fake_runner(command, **kwargs):
                self.assertEqual(command[0], str(exe.resolve()))
                self.assertEqual(command[1:3], ["-ascii", "-b"])
                self.assertEqual(kwargs["cwd"], str(output_dir.resolve()))
                self.assertEqual(kwargs["timeout"], 9)
                netlist = Path(command[3])
                self.assertEqual(netlist.read_text(encoding="ascii"), build_rc_lowpass(1_000, 100e-9).to_spice())
                netlist.with_suffix(".raw").write_text(_raw_text(), encoding="ascii")
                netlist.with_suffix(".log").write_text("LTspice completed", encoding="ascii")
                return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

            result = run_ltspice(build_rc_lowpass(1_000, 100e-9), output_dir, str(exe), 9, fake_runner)
            self.assertTrue(result.raw_path.is_file())
            self.assertTrue(result.report_path.is_file())
            report = json.loads(result.report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["simulation_status"], "completed")
            self.assertEqual(report["verification_status"], "not_evaluated")
            self.assertFalse(report["design_pass"])

    def test_runner_rejects_nonzero_exit_and_does_not_claim_completion(self):
        project_root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(dir=project_root) as temporary:
            root = Path(temporary)
            exe = root / "LTspice.exe"
            exe.write_text("test executable")
            def fail_runner(command, **kwargs):
                return type("Completed", (), {"returncode": 1, "stdout": "", "stderr": "failed"})()
            with self.assertRaisesRegex(SimulationError, "exited with status 1: failed"):
                run_ltspice(build_rc_lowpass(1_000, 100e-9), root / "run", str(exe), runner=fail_runner)


if __name__ == "__main__":
    unittest.main()
