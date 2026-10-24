import json
import io
import math
import os
from pathlib import Path
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from circuitpilot.ir import CircuitIR
from circuitpilot.rc_request import (
    NoFeasibleCandidateError,
    load_rc_request,
    synthesize_rc_request,
    validate_rc_request,
)
from circuitpilot.rc_request_simulation import simulate_rc_request
from circuitpilot.rc_request_refinement import refine_rc_request
from circuitpilot.schematic import render_asc
from circuitpilot.simulation import BatchRunResult, estimate_one_pole_passband_gain, parse_ascii_ac_raw


class RCRequestTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[2]
        self.example_path = self.root / "docs" / "example-rc-lowpass-request.json"
        self.request = {
            "schema_version": 1,
            "topology": "rc_lowpass",
            "behavior": {"cutoff_hz": {"min": 1500.0, "max": 1700.0, "target": 1591.55}},
            "components": {
                "resistance_ohm": {"min": 1000.0, "max": 10000.0, "series": "E12"},
                "capacitance_f": {"min": 5e-8, "max": 1.5e-7, "series": "E12"},
            },
        }

    def test_documented_request_selects_standard_values_and_returns_circuit_ir(self):
        request = load_rc_request(self.example_path)
        result = synthesize_rc_request(request)

        self.assertIsInstance(result.circuit_ir, CircuitIR)
        self.assertEqual(result.report["status"], "feasible")
        self.assertEqual(result.report["candidate"]["resistance_ohm"], 1000.0)
        self.assertEqual(result.report["candidate"]["capacitance_f"], 1e-7)
        self.assertAlmostEqual(result.report["candidate"]["cutoff_hz"], 1591.5494309189535)
        self.assertIn("R1 in out 1000", result.circuit_ir.render())
        self.assertIn("C1 out 0 1e-07", result.circuit_ir.render())
        self.assertTrue(all(item["pass"] for item in result.report["hard_constraints"]))
        self.assertEqual(result.report["constraint_evidence"], "analytical")
        self.assertEqual(result.report["verification_status"], "not_simulated")
        self.assertFalse(result.report["production_qualified"])
        self.assertTrue(all(item["evidence"] == "analytical" for item in result.report["hard_constraints"]))

    def test_synthesis_is_deterministic_and_target_is_a_ranking_preference(self):
        first = synthesize_rc_request(self.request)
        second = synthesize_rc_request(self.request)
        self.assertEqual(first.circuit_ir, second.circuit_ir)
        self.assertEqual(first.report, second.report)
        self.assertAlmostEqual(first.report["candidate"]["cutoff_error_percent_vs_target"],
                               100.0 * (1591.5494309189535 / 1591.55 - 1.0))

        targetless = json.loads(json.dumps(self.request))
        del targetless["behavior"]["cutoff_hz"]["target"]
        result = synthesize_rc_request(targetless)
        self.assertAlmostEqual(result.report["candidate_search"]["ranking_reference_hz"],
                               math.sqrt(1500.0 * 1700.0))
        self.assertIsNone(result.report["candidate"]["cutoff_error_percent_vs_target"])

    def test_request_validation_rejects_ambiguous_or_unsupported_fields(self):
        cases = []
        missing_bound = json.loads(json.dumps(self.request))
        del missing_bound["components"]["resistance_ohm"]["min"]
        cases.append((missing_bound, "missing field"))

        extra = json.loads(json.dumps(self.request))
        extra["behavior"]["cutoff_hz"]["tolerance_percent"] = 2
        cases.append((extra, "unsupported field"))

        invalid_series = json.loads(json.dumps(self.request))
        invalid_series["components"]["capacitance_f"]["series"] = "E96"
        cases.append((invalid_series, "series must be one of"))

        target_outside = json.loads(json.dumps(self.request))
        target_outside["behavior"]["cutoff_hz"]["target"] = 1800
        cases.append((target_outside, "target must lie within"))

        bad_range = json.loads(json.dumps(self.request))
        bad_range["behavior"]["cutoff_hz"]["min"] = 1800
        cases.append((bad_range, "min must be less than or equal to max"))

        null_target = json.loads(json.dumps(self.request))
        null_target["behavior"]["cutoff_hz"]["target"] = None
        cases.append((null_target, "target must be omitted"))

        for request, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                validate_rc_request(request)

    def test_boolean_nonfinite_and_out_of_supported_component_bounds_are_rejected(self):
        for value in (True, math.nan, math.inf, -1, 0):
            request = json.loads(json.dumps(self.request))
            request["behavior"]["cutoff_hz"]["min"] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "finite positive"):
                validate_rc_request(request)

        request = json.loads(json.dumps(self.request))
        request["components"]["resistance_ohm"]["max"] = 1e15
        with self.assertRaisesRegex(ValueError, "within"):
            validate_rc_request(request)

    def test_operating_conditions_and_passband_gain_validation_is_strict(self):
        valid = json.loads(json.dumps(self.request))
        valid["operating_conditions"] = {
            "source_resistance_ohm": 0.0,
            "load_resistance_ohm": None,
        }
        valid["behavior"]["passband_gain"] = {"min": 0.8, "max": 1.0}
        normalized = validate_rc_request(valid)
        self.assertEqual(normalized["operating_conditions"], valid["operating_conditions"])
        self.assertEqual(normalized["behavior"]["passband_gain"], {"min": 0.8, "max": 1.0})

        invalid_requests = []
        for value in (-1.0, True, math.inf):
            bad = json.loads(json.dumps(valid))
            bad["operating_conditions"]["source_resistance_ohm"] = value
            invalid_requests.append((bad, "source_resistance_ohm must be a finite nonnegative"))
        for value in (0.0, -1.0, True, math.inf):
            bad = json.loads(json.dumps(valid))
            bad["operating_conditions"]["load_resistance_ohm"] = value
            invalid_requests.append((bad, "load_resistance_ohm must be a finite positive"))
        bad = json.loads(json.dumps(valid))
        bad["operating_conditions"]["extra"] = 1
        invalid_requests.append((bad, "operating_conditions has unsupported field"))
        for bounds in ({"min": 0, "max": 1}, {"min": 0.8, "max": 1.1},
                       {"min": 0.9, "max": 0.8}):
            bad = json.loads(json.dumps(valid))
            bad["behavior"]["passband_gain"] = bounds
            invalid_requests.append((bad, "passband_gain"))
        for bad, message in invalid_requests:
            with self.subTest(request=bad), self.assertRaisesRegex(ValueError, message):
                validate_rc_request(bad)

    def test_loaded_and_open_load_analytical_cutoff_and_gain(self):
        loaded_example = load_rc_request(self.root / "docs" / "example-rc-lowpass-loaded-request.json")
        loaded_example_result = synthesize_rc_request(loaded_example)
        self.assertAlmostEqual(loaded_example_result.report["candidate"]["passband_gain"],
                               10000.0 / 11100.0)
        self.assertIn("Rload out 0 10000", loaded_example_result.circuit_ir.render())

        loaded = json.loads(json.dumps(self.request))
        loaded["operating_conditions"] = {
            "source_resistance_ohm": 100.0,
            "load_resistance_ohm": 10000.0,
        }
        loaded["behavior"]["cutoff_hz"] = {"min": 1500.0, "max": 1700.0}
        loaded["behavior"]["passband_gain"] = {"min": 0.89, "max": 0.92}
        result = synthesize_rc_request(loaded)
        candidate = result.report["candidate"]
        rth = 1.0 / (1.0 / 1100.0 + 1.0 / 10000.0)
        expected_cutoff = 1.0 / (2.0 * math.pi * 1e-7 * rth)
        self.assertAlmostEqual(candidate["cutoff_hz"], expected_cutoff)
        self.assertAlmostEqual(candidate["passband_gain"], 10000.0 / 11100.0)
        self.assertTrue(all(constraint["pass"] for constraint in result.report["hard_constraints"]))
        netlist = result.circuit_ir.render()
        self.assertIn("V1 src 0 AC 1", netlist)
        self.assertIn("Rsource src in 100", netlist)
        self.assertIn("Rload out 0 10000", netlist)

        open_load = json.loads(json.dumps(loaded))
        open_load["operating_conditions"]["load_resistance_ohm"] = None
        open_load["behavior"]["cutoff_hz"] = {"min": 1400.0, "max": 1500.0}
        open_load["behavior"]["passband_gain"] = {"min": 0.99, "max": 1.0}
        open_result = synthesize_rc_request(open_load)
        self.assertAlmostEqual(open_result.report["candidate"]["cutoff_hz"],
                               1.0 / (2.0 * math.pi * 1100.0 * 1e-7))
        self.assertEqual(open_result.report["candidate"]["passband_gain"], 1.0)
        self.assertNotIn("Rload", open_result.circuit_ir.render())

        legacy = synthesize_rc_request(self.request)
        self.assertNotIn("operating_conditions", validate_rc_request(self.request))
        self.assertIn("V1 in 0 AC 1", legacy.circuit_ir.render())
        self.assertNotIn("Rsource", legacy.circuit_ir.render())

    def test_no_joint_analytical_candidate_reports_cutoff_and_gain_separately(self):
        request = json.loads(json.dumps(self.request))
        request["behavior"]["cutoff_hz"] = {"min": 1500.0, "max": 1700.0}
        request["behavior"]["passband_gain"] = {"min": 0.8, "max": 0.9}
        request["operating_conditions"] = {"source_resistance_ohm": 0.0, "load_resistance_ohm": 1000.0}
        with self.assertRaises(NoFeasibleCandidateError) as captured:
            synthesize_rc_request(request)
        report = captured.exception.report
        self.assertEqual(report["status"], "infeasible")
        checks = {item["name"]: item for item in report["hard_constraints"]}
        self.assertTrue(checks["cutoff_hz"]["pass"])
        self.assertFalse(checks["passband_gain"]["pass"])
        self.assertEqual(report["verification_status"], "not_simulated")
        self.assertFalse(report["production_qualified"])

    def test_refinement_prioritizes_analytically_allowed_gain_but_accepts_only_measurements(self):
        request = _multi_candidate_request()
        request["behavior"]["cutoff_hz"] = {"min": 1300.0, "max": 1700.0, "target": 1591.55}
        request["behavior"]["passband_gain"] = {"min": 0.45, "max": 0.46}
        request["operating_conditions"] = {"source_resistance_ohm": 0.0, "load_resistance_ohm": 1000.0}
        with TemporaryDirectory(dir=self.root) as temp_dir:
            root = Path(temp_dir)
            executable = root / "LTspice.exe"
            executable.write_text("fake executable", encoding="ascii")
            run = refine_rc_request(
                request,
                root / "gain-refinement",
                simulation_budget=1,
                executable=str(executable),
                runner=_fake_ltspice_runner(cutoff_hz=1400.0, passband_gain=0.455),
            )
            first = run.history["attempts"][0]
            saved_ir = json.loads((root / "gain-refinement" / "candidate-001" / "circuit-ir.json")
                                  .read_text(encoding="utf-8"))
        self.assertEqual(first["candidate"]["resistance_ohm"], 1200.0)
        self.assertTrue(first["design_pass"])
        self.assertAlmostEqual(first["measured_passband_gain_estimate_from_complex_ac"], 0.455)
        self.assertTrue(run.report["design_pass"])
        self.assertFalse(run.report["production_qualified"])
        self.assertEqual(saved_ir, run.selected_result.circuit_ir.to_dict())

    def test_simulation_verifies_cutoff_relative_to_complex_asymptote_gain_and_gain_separately(self):
        request = json.loads(json.dumps(self.request))
        request["behavior"]["passband_gain"] = {"min": 0.899999, "max": 0.91}
        request["operating_conditions"] = {
            "source_resistance_ohm": 100.0,
            "load_resistance_ohm": 10000.0,
        }
        with TemporaryDirectory(dir=self.root) as temp_dir:
            root = Path(temp_dir)
            executable = root / "LTspice.exe"
            executable.write_text("fake executable", encoding="ascii")
            passed = simulate_rc_request(
                request, root / "loaded-pass", executable=str(executable),
                runner=_fake_ltspice_runner(cutoff_hz=1600.0, passband_gain=0.9),
            )
            gain_failed = simulate_rc_request(
                request, root / "loaded-gain-fail", executable=str(executable),
                runner=_fake_ltspice_runner(cutoff_hz=1600.0, passband_gain=0.899),
            )
            ir_path = root / "loaded-pass" / "circuit-ir.json"
            circuit_path = root / "loaded-pass" / "design.cir"
            asc_path = root / "loaded-pass" / "design.asc"
            saved_ir = json.loads(ir_path.read_text(encoding="utf-8"))
            saved_circuit = circuit_path.read_text(encoding="ascii")
            saved_asc = asc_path.read_text(encoding="utf-8")

        self.assertEqual(passed.report["verification_status"], "passed")
        self.assertAlmostEqual(passed.report["measurements"]["cutoff_hz"], 1600.0, places=1)
        self.assertAlmostEqual(passed.report["measurements"]["passband_gain_estimate_from_complex_ac"], 0.9)
        self.assertEqual(len(passed.report["hard_constraints"]), 2)
        self.assertTrue(all(item["pass"] for item in passed.report["hard_constraints"]))
        self.assertEqual(saved_ir, passed.synthesis_result.circuit_ir.to_dict())
        self.assertEqual(saved_circuit, passed.synthesis_result.circuit_ir.render())
        self.assertEqual(saved_asc, render_asc(passed.synthesis_result.circuit_ir,
                                               sheet_title="CircuitPilot RC low-pass"))
        self.assertEqual(gain_failed.report["verification_status"], "failed")
        self.assertTrue(gain_failed.report["hard_constraints"][0]["pass"])
        self.assertFalse(gain_failed.report["hard_constraints"][1]["pass"])
        self.assertFalse(gain_failed.report["design_pass"])

    def test_no_candidate_raises_with_fail_closed_constraint_report(self):
        request = json.loads(json.dumps(self.request))
        request["behavior"]["cutoff_hz"] = {"min": 1e6, "max": 1.1e6}

        with self.assertRaises(NoFeasibleCandidateError) as captured:
            synthesize_rc_request(request)

        self.assertEqual(captured.exception.report["status"], "infeasible")
        self.assertIsNone(captured.exception.report["candidate"])
        self.assertEqual(captured.exception.report["candidate_search"]["feasible_candidate_count"], 0)
        self.assertFalse(captured.exception.report["hard_constraints"][0]["pass"])
        self.assertEqual(captured.exception.report["constraint_evidence"], "analytical")
        self.assertEqual(captured.exception.report["verification_status"], "not_simulated")
        self.assertFalse(captured.exception.report["production_qualified"])
        self.assertTrue(all(item["evidence"] == "analytical"
                            for item in captured.exception.report["hard_constraints"]))

    def test_no_standard_component_value_reports_empty_search_axis(self):
        request = json.loads(json.dumps(self.request))
        request["components"]["resistance_ohm"] = {"min": 1010.0, "max": 1020.0, "series": "E12"}

        with self.assertRaises(NoFeasibleCandidateError) as captured:
            synthesize_rc_request(request)

        self.assertEqual(captured.exception.report["candidate_search"]["resistance_values_considered"], 0)
        self.assertFalse(captured.exception.report["hard_constraints"][1]["pass"])

    def test_cli_emits_circuit_and_report_json(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.root / "engine")
        result = subprocess.run(
            [sys.executable, "-m", "circuitpilot.design_request_cli", "--request", str(self.example_path)],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["synthesis_report"]["status"], "feasible")
        self.assertEqual(output["circuit_ir"]["elements"][1]["reference"], "R1")
        self.assertIn(".end", output["netlist"])
        self.assertEqual(output["synthesis_report"]["constraint_evidence"], "analytical")
        self.assertEqual(output["synthesis_report"]["verification_status"], "not_simulated")
        self.assertFalse(output["synthesis_report"]["production_qualified"])

        # The CLI schematic is rendered from the returned CircuitIR, preserving each
        # component's ordered node connectivity.
        asc_by_reference = {}
        for block in output["asc"].split("SYMBOL ")[1:]:
            lines = block.splitlines()
            reference = next(line.split()[-1] for line in lines if line.startswith("SYMATTR InstName "))
            asc_by_reference[reference] = tuple(
                line.split()[-1] for line in lines if line.startswith("FLAG ")
            )
        elements = {item["reference"]: tuple(item["nodes"])
                    for item in output["circuit_ir"]["elements"]}
        self.assertEqual(asc_by_reference["R1"], elements["R1"])
        self.assertEqual(asc_by_reference["C1"], elements["C1"])

    def test_cli_reports_infeasible_request_and_exits_nonzero(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.root / "engine")
        request = json.loads(json.dumps(self.request))
        request["behavior"]["cutoff_hz"] = {"min": 1e6, "max": 1.1e6}
        with TemporaryDirectory(dir=self.root) as temp_dir:
            request_path = Path(temp_dir) / "infeasible-request.json"
            request_path.write_text(json.dumps(request), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-m", "circuitpilot.design_request_cli", "--request", str(request_path)],
                cwd=self.root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        output = json.loads(result.stdout)
        self.assertEqual(output["synthesis_report"]["status"], "infeasible")
        self.assertIsNone(output["synthesis_report"]["candidate"])
        self.assertEqual(output["synthesis_report"]["constraint_evidence"], "analytical")
        self.assertEqual(output["synthesis_report"]["verification_status"], "not_simulated")
        self.assertFalse(output["synthesis_report"]["production_qualified"])

    def test_simulation_verifies_only_measured_cutoff_and_writes_same_ir_artifacts(self):
        with TemporaryDirectory(dir=self.root) as temp_dir:
            root = Path(temp_dir)
            executable = root / "LTspice.exe"
            executable.write_text("fake executable", encoding="ascii")
            output_dir = root / "fresh-pass-run"
            run = simulate_rc_request(
                self.request,
                output_dir,
                executable=str(executable),
                runner=_fake_ltspice_runner(cutoff_hz=1600.0),
            )

            self.assertEqual(run.report["analytical_candidate_feasibility"]["evidence"], "analytical")
            self.assertTrue(run.report["analytical_candidate_feasibility"]["pass"])
            self.assertEqual(run.report["simulation_status"], "completed")
            self.assertEqual(run.report["verification_status"], "passed")
            self.assertTrue(run.report["design_pass"])
            self.assertFalse(run.report["production_qualified"])
            self.assertAlmostEqual(run.report["measurements"]["cutoff_hz"], 1600.0, places=2)
            self.assertTrue(all((output_dir / name).is_file() for name in (
                "design.cir", "design.asc", "circuit-ir.json", "request.json", "bom.json",
                "design.raw", "design.log", "request-verification.json",
            )))
            self.assertEqual((output_dir / "design.cir").read_text(encoding="ascii"),
                             run.synthesis_result.circuit_ir.render())
            saved_ir = json.loads((output_dir / "circuit-ir.json").read_text(encoding="utf-8"))
            self.assertEqual(saved_ir, run.synthesis_result.circuit_ir.to_dict())
            self.assertEqual((output_dir / "design.asc").read_text(encoding="utf-8"),
                             render_asc(run.synthesis_result.circuit_ir, sheet_title="CircuitPilot RC low-pass"))

    def test_out_of_range_measured_cutoff_fails_request_verification(self):
        with TemporaryDirectory(dir=self.root) as temp_dir:
            root = Path(temp_dir)
            executable = root / "LTspice.exe"
            executable.write_text("fake executable", encoding="ascii")
            run = simulate_rc_request(
                self.request,
                root / "fresh-fail-run",
                executable=str(executable),
                runner=_fake_ltspice_runner(cutoff_hz=1800.0),
            )
        self.assertTrue(run.report["analytical_candidate_feasibility"]["pass"])
        self.assertEqual(run.report["simulation_status"], "completed")
        self.assertEqual(run.report["verification_status"], "failed")
        self.assertFalse(run.report["design_pass"])
        self.assertFalse(run.report["hard_constraints"][0]["pass"])
        self.assertEqual(run.report["failure_kind"], "constraint_failed")

    def test_simulation_launch_failure_writes_fail_closed_report(self):
        with TemporaryDirectory(dir=self.root) as temp_dir:
            root = Path(temp_dir)
            executable = root / "LTspice.exe"
            executable.write_text("fake executable", encoding="ascii")
            run = simulate_rc_request(
                self.request,
                root / "fresh-launch-failure",
                executable=str(executable),
                runner=_fake_ltspice_runner(outcome="launch_failure"),
            )
            saved_report = json.loads(run.report_path.read_text(encoding="utf-8"))
        self.assertEqual(run.report["simulation_status"], "failed")
        self.assertEqual(run.report["verification_status"], "failed")
        self.assertFalse(run.report["design_pass"])
        self.assertFalse(run.report["hard_constraints"][0]["pass"])
        self.assertEqual(saved_report["failure_kind"], "simulation_failed")

    def test_missing_cutoff_measurement_fails_closed(self):
        with TemporaryDirectory(dir=self.root) as temp_dir:
            root = Path(temp_dir)
            executable = root / "LTspice.exe"
            executable.write_text("fake executable", encoding="ascii")
            run = simulate_rc_request(
                self.request,
                root / "fresh-missing-measurement",
                executable=str(executable),
                runner=_fake_ltspice_runner(outcome="missing_crossing"),
            )
            zero_passband = simulate_rc_request(
                self.request,
                root / "fresh-zero-passband",
                executable=str(executable),
                runner=_fake_ltspice_runner(passband_gain=0.0),
            )
        self.assertEqual(run.report["simulation_status"], "completed")
        self.assertEqual(run.report["verification_status"], "failed")
        self.assertFalse(run.report["design_pass"])
        self.assertFalse(run.report["hard_constraints"][0]["pass"])
        self.assertIsNone(run.report["measurements"]["cutoff_hz"])
        self.assertEqual(run.report["failure_kind"], "measurement_failed")
        self.assertEqual(zero_passband.report["failure_kind"], "measurement_failed")
        self.assertFalse(zero_passband.report["design_pass"])
        self.assertIsNone(zero_passband.report["measurements"]["passband_gain_estimate_from_complex_ac"])

    def test_complex_one_pole_samples_recover_dc_gain_and_avoid_sweep_start_bias(self):
        raw = _fake_raw_text(1600.0, source_node="in", passband_gain=0.9)
        points = parse_ascii_ac_raw(raw, input_name="V(in)", output_name="V(out)")
        estimated = estimate_one_pole_passband_gain(points)
        sweep_start_magnitude = points[0].output_magnitude / points[0].input_magnitude
        self.assertAlmostEqual(estimated, 0.9, places=14)
        self.assertLess(sweep_start_magnitude, estimated)
        self.assertAlmostEqual(sweep_start_magnitude / estimated, 1.0 / math.sqrt(1.01))

    def test_missing_raw_file_fails_closed_and_reports_available_artifacts(self):
        with TemporaryDirectory(dir=self.root) as temp_dir:
            root = Path(temp_dir)
            executable = root / "LTspice.exe"
            executable.write_text("fake executable", encoding="ascii")
            run = simulate_rc_request(
                self.request,
                root / "fresh-missing-raw",
                executable=str(executable),
                runner=_fake_ltspice_runner(outcome="missing_raw"),
            )
        self.assertEqual(run.report["simulation_status"], "failed")
        self.assertEqual(run.report["verification_status"], "failed")
        self.assertFalse(run.report["design_pass"])
        self.assertTrue(run.report["artifacts_present"]["netlist"])
        self.assertTrue(run.report["artifacts_present"]["log"])
        self.assertFalse(run.report["artifacts_present"]["raw"])

    def test_simulation_timeout_fails_closed(self):
        with TemporaryDirectory(dir=self.root) as temp_dir:
            root = Path(temp_dir)
            executable = root / "LTspice.exe"
            executable.write_text("fake executable", encoding="ascii")
            run = simulate_rc_request(
                self.request,
                root / "fresh-timeout",
                executable=str(executable),
                runner=_fake_ltspice_runner(outcome="timeout"),
            )
        self.assertEqual(run.report["simulation_status"], "failed")
        self.assertEqual(run.report["verification_status"], "failed")
        self.assertFalse(run.report["design_pass"])
        self.assertEqual(run.report["failure_kind"], "simulation_failed")

    def test_simulation_refuses_existing_output_directory_without_modifying_it(self):
        with TemporaryDirectory(dir=self.root) as temp_dir:
            output_dir = Path(temp_dir) / "already-used"
            output_dir.mkdir()
            sentinel = output_dir / "keep.txt"
            sentinel.write_text("previous artifact", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "Refusing to overwrite"):
                simulate_rc_request(self.request, output_dir)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "previous artifact")

    def test_simulation_adapts_sweep_to_valid_cutoff_above_one_megahertz(self):
        request = {
            "schema_version": 1,
            "topology": "rc_lowpass",
            "behavior": {"cutoff_hz": {"min": 10.5e6, "max": 10.7e6}},
            "components": {
                "resistance_ohm": {"min": 1000.0, "max": 1000.0, "series": "E12"},
                "capacitance_f": {"min": 15e-12, "max": 15e-12, "series": "E12"},
            },
        }
        with TemporaryDirectory(dir=self.root) as temp_dir:
            root = Path(temp_dir)
            executable = root / "LTspice.exe"
            executable.write_text("fake executable", encoding="ascii")
            output_dir = root / "high-frequency-run"
            run = simulate_rc_request(
                request,
                output_dir,
                executable=str(executable),
                runner=_fake_ltspice_runner(cutoff_hz=10.6e6),
            )

            expected = run.synthesis_result.report["candidate"]["cutoff_hz"]
            sweep = run.report["ac_sweep_hz"]
            self.assertGreater(expected, 1e6)
            self.assertLess(sweep["min"], expected)
            self.assertGreater(sweep["max"], expected)
            directive = next(line for line in run.synthesis_result.circuit_ir.directives if line.startswith(".ac "))
            tokens = directive.split()
            self.assertAlmostEqual(float(tokens[-2]), sweep["min"], places=2)
            self.assertAlmostEqual(float(tokens[-1]), sweep["max"], places=2)
            self.assertIn("!" + directive, (output_dir / "design.asc").read_text(encoding="utf-8"))
            self.assertEqual((output_dir / "design.cir").read_text(encoding="ascii"),
                             run.synthesis_result.circuit_ir.render())

    def test_cli_simulate_mode_invokes_request_runner_and_reports_failure_exit(self):
        from circuitpilot import design_request_cli

        with TemporaryDirectory(dir=self.root) as temp_dir:
            root = Path(temp_dir)
            output_dir = root / "cli-simulation"

            def fake_run_spice(circuit, stem, folder, executable=None, timeout_seconds=120.0, runner=subprocess.run):
                netlist = Path(folder) / f"{stem}.cir"
                raw = Path(folder) / f"{stem}.raw"
                log = Path(folder) / f"{stem}.log"
                op_raw = Path(folder) / f"{stem}.op.raw"
                netlist.write_text(circuit.render(), encoding="ascii")
                log.write_text("fake LTspice run", encoding="ascii")
                raw.write_text(_fake_raw_text(1800.0), encoding="ascii")
                return BatchRunResult(netlist, raw, log, op_raw, "fake-ltspice")

            stdout = io.StringIO()
            with patch("circuitpilot.rc_request_simulation.run_spice", side_effect=fake_run_spice):
                with redirect_stdout(stdout):
                    exit_code = design_request_cli.main([
                        "--request", str(self.example_path), "--simulate", "--output-dir", str(output_dir),
                    ])
            emitted = json.loads(stdout.getvalue())

        self.assertEqual(exit_code, 1)
        self.assertEqual(emitted["verification_report"]["verification_status"], "failed")
        self.assertFalse(emitted["verification_report"]["design_pass"])

    def test_cli_refine_requires_simulate_and_explicit_budget(self):
        from circuitpilot import design_request_cli

        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as not_simulating:
                design_request_cli.main(["--request", str(self.example_path), "--refine"])
        self.assertEqual(not_simulating.exception.code, 2)

        with TemporaryDirectory(dir=self.root) as temp_dir:
            with redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as no_budget:
                    design_request_cli.main([
                        "--request", str(self.example_path), "--simulate", "--refine",
                        "--output-dir", str(Path(temp_dir) / "fresh-output"),
                    ])
        self.assertEqual(no_budget.exception.code, 2)

    def test_refinement_obeys_budget_and_preserves_ordered_candidate_artifacts(self):
        request = _multi_candidate_request()
        observed = []
        with TemporaryDirectory(dir=self.root) as temp_dir:
            root = Path(temp_dir)
            executable = root / "LTspice.exe"
            executable.write_text("fake executable", encoding="ascii")
            output_dir = root / "budget-run"
            run = refine_rc_request(
                request,
                output_dir,
                simulation_budget=1,
                executable=str(executable),
                runner=_sequenced_ltspice_runner([1300.0], observed),
            )

            self.assertEqual(run.report["status"], "failed")
            self.assertEqual(run.report["budget_used"], 1)
            self.assertEqual(run.report["stop_reason"], "simulation_budget_exhausted")
            self.assertEqual(len(run.history["attempts"]), 1)
            self.assertEqual(observed[0], {"resistance_ohm": 1000.0, "capacitance_f": 1e-7})
            self.assertTrue(all((output_dir / "candidate-001" / name).is_file() for name in (
                "design.cir", "design.asc", "circuit-ir.json", "request.json", "bom.json",
                "design.raw", "design.log", "request-verification.json",
            )))
            self.assertTrue(run.history_path.is_file())
            self.assertTrue(run.report_path.is_file())
            self.assertEqual(json.loads(run.history_path.read_text(encoding="utf-8"))["budget_used"], 1)

    def test_refinement_has_deterministic_candidate_order_across_runs(self):
        request = _multi_candidate_request()
        orders = []
        with TemporaryDirectory(dir=self.root) as temp_dir:
            root = Path(temp_dir)
            executable = root / "LTspice.exe"
            executable.write_text("fake executable", encoding="ascii")
            for name in ("deterministic-a", "deterministic-b"):
                observed = []
                run = refine_rc_request(
                    request,
                    root / name,
                    simulation_budget=3,
                    executable=str(executable),
                    runner=_sequenced_ltspice_runner([1300.0, 1300.0, 1300.0], observed),
                )
                orders.append([attempt["candidate"] for attempt in run.history["attempts"]])
        self.assertEqual(orders[0], orders[1])
        self.assertEqual(len(orders[0]), 3)

    def test_refinement_stops_on_first_measured_pass(self):
        request = _multi_candidate_request()
        observed = []
        with TemporaryDirectory(dir=self.root) as temp_dir:
            root = Path(temp_dir)
            executable = root / "LTspice.exe"
            executable.write_text("fake executable", encoding="ascii")
            output_dir = root / "early-pass"
            run = refine_rc_request(
                request,
                output_dir,
                simulation_budget=4,
                executable=str(executable),
                runner=_sequenced_ltspice_runner([1300.0, 1500.0, 1591.5], observed),
            )

        self.assertEqual(run.report["status"], "passed")
        self.assertEqual(run.report["budget_used"], 2)
        self.assertEqual(run.report["stop_reason"], "first_measured_pass")
        self.assertTrue(run.report["design_pass"])
        self.assertEqual(len(run.history["attempts"]), 2)
        self.assertEqual(observed[1], {"resistance_ohm": 1000.0, "capacitance_f": 1.2e-7})
        self.assertFalse(run.history["attempts"][1]["analytical_candidate_feasible"])
        self.assertTrue(run.history["attempts"][1]["design_pass"])
        self.assertFalse(run.report["production_qualified"])
        self.assertFalse((output_dir / "candidate-003").exists())

    def test_refinement_exhausts_candidate_space_without_a_measured_pass(self):
        request = _multi_candidate_request()
        observed = []
        with TemporaryDirectory(dir=self.root) as temp_dir:
            root = Path(temp_dir)
            executable = root / "LTspice.exe"
            executable.write_text("fake executable", encoding="ascii")
            output_dir = root / "exhausted"
            run = refine_rc_request(
                request,
                output_dir,
                simulation_budget=10,
                executable=str(executable),
                runner=_sequenced_ltspice_runner([1300.0], observed),
            )

            self.assertEqual(run.report["status"], "failed")
            self.assertEqual(run.report["budget_used"], 4)
            self.assertEqual(run.report["candidate_space_size"], 4)
            self.assertEqual(run.report["stop_reason"], "candidate_space_exhausted")
            self.assertIsNone(run.report["selected_candidate"])
            self.assertEqual(len(run.history["attempts"]), 4)
            for attempt in run.history["attempts"]:
                folder = output_dir / attempt["folder"]
                self.assertTrue(all((folder / name).is_file() for name in (
                    "design.cir", "design.asc", "circuit-ir.json", "request.json", "bom.json",
                    "design.raw", "design.log", "request-verification.json",
                )))
                saved_ir_data = json.loads((folder / "circuit-ir.json").read_text(encoding="utf-8"))
                rendered_ir = CircuitIR.from_spice_text((folder / "design.cir").read_text(encoding="ascii"))
                self.assertEqual(saved_ir_data, rendered_ir.to_dict())
                self.assertEqual(
                    (folder / "design.asc").read_text(encoding="utf-8"),
                    render_asc(rendered_ir, sheet_title="CircuitPilot RC low-pass"),
                )

    def test_refinement_stops_and_fails_closed_on_simulator_or_measurement_failure(self):
        request = _multi_candidate_request()
        with TemporaryDirectory(dir=self.root) as temp_dir:
            root = Path(temp_dir)
            executable = root / "LTspice.exe"
            executable.write_text("fake executable", encoding="ascii")
            run = refine_rc_request(
                request,
                root / "failure-stop",
                simulation_budget=4,
                executable=str(executable),
                runner=_fake_ltspice_runner(outcome="missing_crossing"),
            )
        self.assertEqual(run.report["status"], "failed")
        self.assertFalse(run.report["design_pass"])
        self.assertEqual(run.report["budget_used"], 1)
        self.assertEqual(run.report["stop_reason"], "simulation_or_measurement_failure")
        self.assertEqual(run.history["attempts"][0]["failure_kind"], "measurement_failed")

    def test_refinement_refuses_existing_output_root(self):
        with TemporaryDirectory(dir=self.root) as temp_dir:
            output_dir = Path(temp_dir) / "already-used-refinement"
            output_dir.mkdir()
            sentinel = output_dir / "keep.txt"
            sentinel.write_text("preserve", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "Refusing to overwrite"):
                refine_rc_request(_multi_candidate_request(), output_dir, simulation_budget=2)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")


def _fake_ltspice_runner(cutoff_hz: float = 1600.0, outcome: str = "success", passband_gain: float = 1.0):
    def runner(command, cwd, capture_output, text, timeout, check):
        netlist_path = Path(command[-1])
        source_line = next(line for line in netlist_path.read_text(encoding="ascii").splitlines()
                           if line.startswith("V1 "))
        source_node = source_line.split()[1]
        raw_path = netlist_path.with_suffix(".raw")
        log_path = netlist_path.with_suffix(".log")
        log_path.write_text("fake LTspice run", encoding="ascii")
        if outcome == "launch_failure":
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="simulator error")
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(command, timeout)
        if outcome == "missing_raw":
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        raw_path.write_text(
            _fake_raw_text(cutoff_hz, source_node=source_node, passband_gain=passband_gain,
                           flat_response=outcome == "missing_crossing"),
            encoding="ascii",
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
    return runner


def _multi_candidate_request() -> dict:
    return {
        "schema_version": 1,
        "topology": "rc_lowpass",
        "behavior": {"cutoff_hz": {"min": 1400.0, "max": 1700.0, "target": 1591.55}},
        "components": {
            "resistance_ohm": {"min": 1000.0, "max": 1200.0, "series": "E12"},
            "capacitance_f": {"min": 1e-7, "max": 1.2e-7, "series": "E12"},
        },
    }


def _sequenced_ltspice_runner(measured_cutoffs: list[float], observed: list[dict]):
    def runner(command, cwd, capture_output, text, timeout, check):
        netlist_path = Path(command[-1])
        values = {}
        for line in netlist_path.read_text(encoding="ascii").splitlines():
            tokens = line.split()
            if tokens and tokens[0] in {"R1", "C1"}:
                values[tokens[0]] = float(tokens[3])
        observed.append({
            "resistance_ohm": values["R1"],
            "capacitance_f": values["C1"],
        })
        cutoff = measured_cutoffs[min(len(observed) - 1, len(measured_cutoffs) - 1)]
        raw_path = netlist_path.with_suffix(".raw")
        log_path = netlist_path.with_suffix(".log")
        raw_path.write_text(_fake_raw_text(cutoff), encoding="ascii")
        log_path.write_text("fake LTspice run", encoding="ascii")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
    return runner


def _fake_raw_text(
    cutoff_hz: float,
    source_node: str = "in",
    passband_gain: float = 1.0,
    flat_response: bool = False,
) -> str:
    samples = []
    for index in range(201):
        ratio = 0.1 * (10.0 ** (index / 100.0))
        frequency = cutoff_hz * ratio
        if flat_response:
            output = complex(passband_gain, 0.0)
        else:
            # H(jw) = G0 / (1 + j*w/wc), the complex transfer of a one-pole low-pass.
            output = passband_gain / complex(1.0, ratio)
        samples.append((frequency, output))
    header = (
        "Title: fake AC\n"
        "Plotname: AC Analysis\n"
        "No. Variables: 3\n"
        f"No. Points: {len(samples)}\n"
        "Variables:\n"
        "0 frequency frequency\n"
        f"1 V({source_node}) voltage\n"
        "2 V(out) voltage\n"
    )
    rows = ["Values:\n"]
    for index, (frequency, output) in enumerate(samples):
        rows.extend((f"{index} {frequency:.17g},0\n", " 1,0\n",
                     f" {output.real:.17g},{output.imag:.17g}\n"))
    return header + "".join(rows)


if __name__ == "__main__":
    unittest.main()
