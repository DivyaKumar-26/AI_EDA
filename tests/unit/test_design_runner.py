import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import sys

from circuitpilot.design_runner import (
    _amplifier_measurements, _bias_measurements, _condition_coverage,
    _nominal_spec_circuit, run_example_design,
)
from circuitpilot.designs import EXAMPLE_DESIGNS
from circuitpilot.examples_cli import main as examples_main
from circuitpilot.rawdata import RawPlot
from circuitpilot.simulation import SimulationError


def _spec():
    return {
        "schema_version": 1,
        "design_id": "inverting-opamp-amplifier",
        "operating_conditions": {
            "supply_mode": "dual", "nominal_supply_v": 15, "supply_tolerance_percent": 10,
            "ambient_temperature_c": [0, 27, 70], "resistor_tolerance_percent": 1,
            "capacitor_tolerance_percent": 10, "load_resistance_ohm": 10000,
            "source_resistance_ohm": 50, "input_amplitude_v": 0.01,
            "input_frequency_hz": 1000, "maximum_output_peak_v": 10,
            "maximum_thd_percent": 5,
        },
        "requirements": [{"metric": "gain_at_1khz_magnitude_v_per_v", "unit": "V/V", "min": 9.5, "max": 10.5}],
    }


def _ac_raw():
    return """Title: synthetic
Plotname: AC Analysis
Flags: complex forward log
No. Variables: 3
No. Points: 3
Variables:
\t0\tfrequency\tfrequency
\t1\tV(in)\tvoltage
\t2\tV(out)\tvoltage
Values:
0\t100,0
\t1,0
\t10,0
1\t1000,0
\t1,0
\t10,0
2\t10000,0
\t1,0
\t10,0
"""


def _op_raw():
    return """Title: synthetic
Plotname: Operating Point
Flags: real
No. Variables: 2
No. Points: 1
Variables:
\t0\tV(in)\tvoltage
\t1\tV(out)\tvoltage
Values:
0\t0
\t0
"""


class DesignRunnerTests(unittest.TestCase):
    def test_bjt_amplifier_collector_current_uses_requested_supply(self):
        design = EXAMPLE_DESIGNS["common-emitter-bjt-amplifier"]
        ac = RawPlot("AC Analysis", "complex", ("frequency", "v(in)", "v(out)"), (
            {"frequency": 1000 + 0j, "v(in)": 1 + 0j, "v(out)": 10 + 0j},
            {"frequency": 2000 + 0j, "v(in)": 1 + 0j, "v(out)": 10 + 0j},
        ))
        op = RawPlot("Operating Point", "real", ("v(base)", "v(emitter)", "v(out)"), (
            {"v(base)": 1.7 + 0j, "v(emitter)": 1.0 + 0j, "v(out)": 8.0 + 0j},
        ))
        measured = _amplifier_measurements(design, ac, op, nominal_supply_v=13.5)
        self.assertAlmostEqual(measured["bias"]["collector_current_a_estimate"], (13.5 - 8.0) / 4700)
        self.assertAlmostEqual(measured["bias"]["supply_v"], 13.5)

    def test_bjt_bias_uses_requested_nominal_point_and_current_from_measured_supply(self):
        design = EXAMPLE_DESIGNS["bjt-bias-network"]
        dc = RawPlot("DC Transfer characteristic", "real", ("v(vcc)", "v(base)", "v(emitter)", "v(collector)"), (
            {"v(vcc)": 12.0 + 0j, "v(base)": 1.7 + 0j, "v(emitter)": 1.0 + 0j, "v(collector)": 8.0 + 0j},
            {"v(vcc)": 13.5 + 0j, "v(base)": 1.7 + 0j, "v(emitter)": 1.0 + 0j, "v(collector)": 8.8 + 0j},
            {"v(vcc)": 15.0 + 0j, "v(base)": 1.7 + 0j, "v(emitter)": 1.0 + 0j, "v(collector)": 9.5 + 0j},
        ))
        measured = _bias_measurements(design, dc, nominal_supply_v=13.5)
        self.assertAlmostEqual(measured["nominal_supply_v"], 13.5)
        self.assertAlmostEqual(measured["nominal_bias"]["collector_current_a_estimate"], (13.5 - 8.8) / 4700)

    def test_bias_sweep_includes_off_grid_and_out_of_reference_range_requested_supply(self):
        design = EXAMPLE_DESIGNS["bjt-bias-network"]
        spec = _spec()
        spec["design_id"] = design.design_id
        spec["operating_conditions"].update({
            "supply_mode": "single", "nominal_supply_v": 13.3,
            "source_resistance_ohm": None, "load_resistance_ohm": None,
        })
        circuit = _nominal_spec_circuit(design, spec)
        dc = next(line for line in circuit.directives if line.casefold().startswith(".dc vcc "))
        self.assertEqual(dc, ".dc VCC 9 15 0.1")
        grid = [9 + index * 0.1 for index in range(61)]
        self.assertTrue(any(abs(value - 13.3) < 1e-9 for value in grid))
        self.assertTrue(all(any(abs(value - reference) < 1e-9 for value in grid)
                            for reference in (9, 9.5, 10, 10.5, 11, 11.5, 12, 12.5, 13, 13.5, 14, 14.5, 15)))

        spec["operating_conditions"]["nominal_supply_v"] = 20.0
        circuit = _nominal_spec_circuit(design, spec)
        dc = next(line for line in circuit.directives if line.casefold().startswith(".dc vcc "))
        self.assertEqual(dc, ".dc VCC 9 20 0.5")
        grid = [9 + index * 0.5 for index in range(23)]
        self.assertIn(20, grid)
        self.assertTrue(all(reference in grid for reference in (9, 9.5, 10, 12, 15)))

    def test_bias_fails_closed_if_requested_supply_sample_is_missing_and_reports_exact_evidence(self):
        design = EXAMPLE_DESIGNS["bjt-bias-network"]
        dc = RawPlot("DC Transfer characteristic", "real", ("v(vcc)", "v(base)", "v(emitter)", "v(collector)"), (
            {"v(vcc)": 12.0 + 0j, "v(base)": 1.7 + 0j, "v(emitter)": 1.0 + 0j, "v(collector)": 8.0 + 0j},
            {"v(vcc)": 13.5 + 0j, "v(base)": 1.7 + 0j, "v(emitter)": 1.0 + 0j, "v(collector)": 8.8 + 0j},
        ))
        with self.assertRaisesRegex(SimulationError, "does not contain the requested nominal supply 13.3 V"):
            _bias_measurements(design, dc, nominal_supply_v=13.3)
        spec = _spec()
        spec["design_id"] = design.design_id
        spec["operating_conditions"].update({
            "supply_mode": "single", "nominal_supply_v": 13.3,
            "source_resistance_ohm": None, "load_resistance_ohm": None,
        })
        coverage = _condition_coverage(spec, {"nominal_supply_v": 13.3})
        self.assertEqual(coverage["requested_nominal_supply_v"], 13.3)
        self.assertEqual(coverage["measured_nominal_supply_v"], 13.3)
        self.assertEqual(coverage["nominal_supply_status"], "confirmed_exact")

    def test_missing_requested_bias_sample_is_recorded_as_unconfirmed_in_failed_report(self):
        design = EXAMPLE_DESIGNS["bjt-bias-network"]
        spec = _spec()
        spec["design_id"] = design.design_id
        spec["operating_conditions"].update({
            "supply_mode": "single", "nominal_supply_v": 13.3,
            "source_resistance_ohm": None, "load_resistance_ohm": None,
        })
        spec["requirements"] = [{"metric": "nominal_supply_v", "unit": "V", "min": 13, "max": 14}]
        with TemporaryDirectory(dir=Path.cwd()) as temp:
            root = Path(temp)
            exe = root / "LTspice.exe"
            exe.write_text("placeholder")

            def fake_runner(command, **kwargs):
                netlist = Path(command[3])
                self.assertIn(".dc VCC 9 15 0.1", netlist.read_text(encoding="ascii"))
                netlist.with_suffix(".raw").write_text("""Title: synthetic
Plotname: DC Transfer characteristic
Flags: real
No. Variables: 4
No. Points: 2
Variables:
\t0\tV(vcc)\tvoltage
\t1\tV(base)\tvoltage
\t2\tV(emitter)\tvoltage
\t3\tV(collector)\tvoltage
Values:
0\t12
\t1.7
\t1
\t8
1\t13.5
\t1.7
\t1
\t8.8
""", encoding="ascii")
                netlist.with_suffix(".log").write_text("synthetic test result", encoding="ascii")
                return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

            with patch("circuitpilot.design_runner.discover_ltspice", return_value=exe.resolve()):
                with self.assertRaisesRegex(SimulationError, "does not contain the requested nominal supply 13.3 V"):
                    run_example_design(design, root / "runs", str(exe), runner=fake_runner, spec=spec)
            report_path = root / "runs" / design.design_id / "verification-report.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            coverage = report["operating_conditions_verification"]
            self.assertEqual(coverage["requested_nominal_supply_v"], 13.3)
            self.assertIsNone(coverage["measured_nominal_supply_v"])
            self.assertEqual(coverage["nominal_supply_status"], "not_confirmed")

    def test_nominal_spec_inserts_source_resistance_and_load_in_ir(self):
        design = EXAMPLE_DESIGNS["inverting-opamp-amplifier"]
        circuit = _nominal_spec_circuit(design, _spec())
        elements = {item.reference.casefold(): item for item in circuit.elements}
        self.assertEqual(elements["vin"].nodes, ("__cp_source", "0"))
        self.assertEqual(elements["rcp_source"].nodes, ("__cp_source", "in"))
        self.assertEqual(elements["rcp_source"].body, "50")
        self.assertEqual(elements["rcp_load"].nodes, ("out", "0"))
        self.assertEqual(elements["rcp_load"].body, "10000")

    def test_spec_run_reports_limit_result_and_fails_overall_when_conditions_are_unverified(self):
        design = EXAMPLE_DESIGNS["inverting-opamp-amplifier"]
        with TemporaryDirectory(dir=Path.cwd()) as temp:
            root = Path(temp)
            exe = root / "LTspice.exe"
            exe.write_text("placeholder")

            def fake_runner(command, **kwargs):
                netlist = Path(command[3])
                self.assertIn("RCP_SOURCE __cp_source in 50", netlist.read_text(encoding="ascii"))
                self.assertIn("RCP_LOAD out 0 10000", netlist.read_text(encoding="ascii"))
                netlist.with_suffix(".raw").write_text(_ac_raw(), encoding="ascii")
                netlist.with_suffix(".op.raw").write_text(_op_raw(), encoding="ascii")
                netlist.with_suffix(".log").write_text("synthetic test result", encoding="ascii")
                return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

            with patch("circuitpilot.design_runner.discover_ltspice", return_value=exe.resolve()):
                report_path = run_example_design(design, root / "runs", str(exe), runner=fake_runner, spec=_spec())
            report = json.loads(report_path.read_text(encoding="utf-8"))
            bom = (report_path.parent / "bom.csv").read_text(encoding="utf-8-sig")
            self.assertEqual(report["requirements_evaluation"]["status"], "passed")
            self.assertTrue(report["requirements_evaluation"]["checks"][0]["pass"])
            self.assertEqual(report["operating_conditions_verification"]["status"], "nominal_only")
            self.assertFalse(report["operating_conditions_verification"]["complete"])
            self.assertFalse(report["design_pass"])
            self.assertFalse(report["production_qualified"])
            self.assertIn('RCP_SOURCE,"50 Ω, user requirement",1', bom)
            self.assertIn('RCP_LOAD,"10000 Ω, user requirement",1', bom)

    def test_spec_design_mismatch_rejected_before_creating_output(self):
        design = EXAMPLE_DESIGNS["inverting-opamp-amplifier"]
        spec = _spec()
        spec["design_id"] = "non-inverting-opamp-amplifier"
        with TemporaryDirectory(dir=Path.cwd()) as temp:
            target = Path(temp) / "run"
            with self.assertRaisesRegex(ValueError, "does not match"):
                run_example_design(design, target, spec=spec)
            self.assertFalse(target.exists())

    def test_examples_cli_loads_matching_spec_and_returns_failure_for_incomplete_coverage(self):
        with TemporaryDirectory(dir=Path.cwd()) as temp:
            root = Path(temp)
            spec_path = root / "spec.json"
            spec_path.write_text(json.dumps(_spec()), encoding="utf-8")
            report_path = root / "report.json"
            report_path.write_text(json.dumps({"design_pass": False}), encoding="utf-8")
            args = ["examples_cli", "--design", "inverting-opamp-amplifier", "--spec", str(spec_path),
                    "--output-root", str(root / "output")]
            with patch.object(sys, "argv", args), patch("circuitpilot.examples_cli.run_example_design", return_value=report_path) as run:
                self.assertEqual(examples_main(), 1)
            self.assertEqual(run.call_args.kwargs["spec"]["design_id"], "inverting-opamp-amplifier")


if __name__ == "__main__":
    unittest.main()
