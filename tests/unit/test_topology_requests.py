import json
import math
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest

from circuitpilot.topology_requests import (
    simulate_topology_request,
    synthesize_behavioral_request,
    validate_topology_request,
)
from circuitpilot.schematic import render_asc
from circuitpilot.rc_request import NoFeasibleCandidateError


class AdditionalTopologyTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[2]

    def test_rc_highpass_validation_analytical_cutoff_and_ir(self):
        request = json.loads((self.root / "docs/example-rc-highpass-request.json").read_text(encoding="utf-8"))
        normalized = validate_topology_request(request)
        result = synthesize_behavioral_request(normalized)
        self.assertEqual(result.report["status"], "feasible")
        self.assertAlmostEqual(result.report["candidate"]["cutoff_hz"], 1.0 / (2 * math.pi * 1000 * 1e-7))
        self.assertIn("C1 in out 1e-07", result.circuit_ir.render())
        self.assertIn("R1 out 0 1000", result.circuit_ir.render())
        self.assertEqual(result.report["verification_status"], "not_simulated")

    def test_rl_lowpass_validation_analytical_cutoff_and_ir(self):
        request = json.loads((self.root / "docs/example-rl-lowpass-request.json").read_text(encoding="utf-8"))
        result = synthesize_behavioral_request(request)
        self.assertEqual(result.report["status"], "feasible")
        self.assertAlmostEqual(result.report["candidate"]["cutoff_hz"], 1000.0 / (2 * math.pi * 0.1))
        self.assertIn("L1 in out 0.1", result.circuit_ir.render())
        self.assertIn("R1 out 0 1000", result.circuit_ir.render())
        self.assertIn("SYMBOL ind", render_asc(result.circuit_ir))

    def test_opamp_rc_lowpass_validation_formula_and_same_ir_wiring(self):
        request = json.loads((self.root / "docs/example-opamp-rc-lowpass-request.json").read_text(encoding="utf-8"))
        normalized = validate_topology_request(request)
        result = synthesize_behavioral_request(normalized)
        expected = 1.0 / (2 * math.pi * 1000 * 1e-7)
        self.assertEqual(result.report["status"], "feasible")
        self.assertAlmostEqual(result.report["candidate"]["cutoff_hz"], expected)
        self.assertIn("R1 in filt 1000", result.circuit_ir.render())
        self.assertIn("C1 filt 0 1e-07", result.circuit_ir.render())
        self.assertIn("XU1 filt out out CP_OPAMP", result.circuit_ir.render())
        self.assertIn(".subckt CP_OPAMP INP INM OUT", result.circuit_ir.render())
        asc = render_asc(result.circuit_ir)
        for reference in ("R1", "C1", "XU1"):
            self.assertIn(f"SYMATTR InstName {reference}", asc)
        self.assertIn("SYMATTR Value CP_OPAMP", asc)
        self.assertEqual(result.report["verification_status"], "not_simulated")

    def test_opamp_rc_lowpass_rejects_cutoff_near_macro_bandwidth(self):
        request = json.loads((self.root / "docs/example-opamp-rc-lowpass-request.json").read_text(encoding="utf-8"))
        request["behavior"]["cutoff_hz"] = {"min": 15_000_000, "max": 16_000_000}
        with self.assertRaisesRegex(ValueError, "150000 Hz.*CP_OPAMP follower"):
            validate_topology_request(request)
        try:
            import jsonschema
        except ImportError:
            return
        schema = json.loads((self.root / "schemas/rc-lowpass-request.schema.json").read_text(encoding="utf-8"))
        self.assertFalse(jsonschema.Draft202012Validator(schema).is_valid(request))

    def test_opamp_rc_lowpass_simulation_writes_support_and_measures_cutoff(self):
        request = json.loads((self.root / "docs/example-opamp-rc-lowpass-request.json").read_text(encoding="utf-8"))
        with TemporaryDirectory(dir=self.root) as directory:
            root = Path(directory)
            exe = root / "LTspice.exe"
            exe.write_text("fake", encoding="ascii")
            result, report = simulate_topology_request(
                request, root / "fresh-opamp-rc-run", executable=str(exe), runner=_lowpass_runner())
            folder = root / "fresh-opamp-rc-run"
            saved_ir = json.loads((folder / "circuit-ir.json").read_text(encoding="utf-8"))
            support = (folder / "CP_OPAMP.asy").read_text(encoding="ascii")
            self.assertEqual(saved_ir, result.circuit_ir.to_dict())
            self.assertIn("SYMATTR Value CP_OPAMP", support)
            self.assertIn(".subckt CP_OPAMP", (folder / "design.cir").read_text(encoding="ascii"))
            self.assertIn("CP_OPAMP.asy", report["artifacts"]["support_files"])
        self.assertEqual(report["verification_status"], "passed")
        self.assertEqual(report["cutoff_direction"], "descending")
        self.assertAlmostEqual(report["measurements"]["cutoff_hz"], 1.0 / (2 * math.pi * 1000 * 1e-7), delta=2)
        self.assertFalse(report["production_qualified"])

    def test_opamp_rc_lowpass_missing_raw_fails_closed(self):
        request = json.loads((self.root / "docs/example-opamp-rc-lowpass-request.json").read_text(encoding="utf-8"))
        with TemporaryDirectory(dir=self.root) as directory:
            root = Path(directory)
            exe = root / "LTspice.exe"
            exe.write_text("fake", encoding="ascii")

            def no_raw_runner(command, cwd, capture_output, text, timeout, check):
                Path(command[-1]).with_suffix(".log").write_text("missing raw", encoding="ascii")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            _, report = simulate_topology_request(
                request, root / "missing-opamp-raw", executable=str(exe), runner=no_raw_runner)
            self.assertTrue((root / "missing-opamp-raw/CP_OPAMP.asy").is_file())
        self.assertFalse(report["design_pass"])
        self.assertEqual(report["verification_status"], "failed")
        self.assertIsNone(report["measurements"]["cutoff_hz"])
        self.assertFalse(report["hard_constraints"][0]["pass"])

    def test_inverting_opamp_validation_synthesis_and_same_ir_artifacts(self):
        request = json.loads((self.root / "docs/example-inverting-opamp-request.json").read_text(encoding="utf-8"))
        result = synthesize_behavioral_request(validate_topology_request(request))
        self.assertEqual(result.report["status"], "feasible")
        self.assertEqual(result.report["candidate"]["gain_magnitude"], 10.0)
        netlist = result.circuit_ir.render()
        for line in ("VIN in 0 DC 0 AC 1", "RIN in summing 10000", "RF out summing 100000",
                     "XU1 0 summing out CP_OPAMP", ".ac dec 100 10 100Meg"):
            self.assertIn(line, netlist)
        self.assertIn(".subckt CP_OPAMP INP INM OUT", netlist)
        asc = render_asc(result.circuit_ir)
        for reference in ("RIN", "RF", "XU1"):
            self.assertIn(f"SYMATTR InstName {reference}", asc)
        self.assertEqual(result.report["verification_status"], "not_simulated")

    def test_inverting_opamp_simulation_measures_gain_at_1khz(self):
        request = json.loads((self.root / "docs/example-inverting-opamp-request.json").read_text(encoding="utf-8"))
        with TemporaryDirectory(dir=self.root) as directory:
            root = Path(directory)
            exe = root / "LTspice.exe"
            exe.write_text("fake", encoding="ascii")
            result, report = simulate_topology_request(
                request, root / "fresh-inverting-opamp-run", executable=str(exe), runner=_inverting_opamp_runner())
            folder = root / "fresh-inverting-opamp-run"
            self.assertEqual(json.loads((folder / "circuit-ir.json").read_text(encoding="utf-8")),
                             result.circuit_ir.to_dict())
            self.assertIn(".subckt CP_OPAMP", (folder / "design.cir").read_text(encoding="ascii"))
            self.assertTrue((folder / "CP_OPAMP.asy").is_file())
        self.assertEqual(report["verification_status"], "passed")
        self.assertEqual(report["measurements"]["frequency_hz"], 1000.0)
        self.assertAlmostEqual(report["measurements"]["gain_magnitude"], 10.0)
        self.assertEqual(report["ac_sweep_hz"]["max"], 100_000_000.0)
        self.assertEqual(report["hard_constraints"][0]["name"], "gain_magnitude")
        self.assertFalse(report["production_qualified"])

    def test_inverting_opamp_gain_limit_and_missing_raw_fail_closed(self):
        request = json.loads((self.root / "docs/example-inverting-opamp-request.json").read_text(encoding="utf-8"))
        request["behavior"]["gain_magnitude"] = {"min": 90, "max": 110}
        with self.assertRaisesRegex(ValueError, "100 V/V"):
            validate_topology_request(request)
        try:
            import jsonschema
        except ImportError:
            jsonschema = None
        if jsonschema:
            schema = json.loads((self.root / "schemas/rc-lowpass-request.schema.json").read_text(encoding="utf-8"))
            self.assertFalse(jsonschema.Draft202012Validator(schema).is_valid(request))

        request = json.loads((self.root / "docs/example-inverting-opamp-request.json").read_text(encoding="utf-8"))
        with TemporaryDirectory(dir=self.root) as directory:
            root = Path(directory)
            exe = root / "LTspice.exe"
            exe.write_text("fake", encoding="ascii")

            def no_raw_runner(command, cwd, capture_output, text, timeout, check):
                Path(command[-1]).with_suffix(".log").write_text("missing raw", encoding="ascii")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            _, report = simulate_topology_request(
                request, root / "missing-inverting-opamp-raw", executable=str(exe), runner=no_raw_runner)
        self.assertEqual(report["verification_status"], "failed")
        self.assertIsNone(report["measurements"]["gain_magnitude"])
        self.assertFalse(report["hard_constraints"][0]["pass"])

    def test_validation_rejects_missing_or_mismatched_component_bounds(self):
        request = json.loads((self.root / "docs/example-rl-lowpass-request.json").read_text(encoding="utf-8"))
        del request["components"]["inductance_h"]
        with self.assertRaisesRegex(ValueError, "inductance_h"):
            validate_topology_request(request)
        request["topology"] = "rc_highpass"
        with self.assertRaisesRegex(ValueError, "capacitance_f"):
            validate_topology_request(request)

    def test_non_string_topology_is_rejected_as_value_error(self):
        request = json.loads((self.root / "docs/example-rc-highpass-request.json").read_text(encoding="utf-8"))
        for topology in ([], {}, 1, None):
            request["topology"] = topology
            with self.subTest(topology=topology), self.assertRaisesRegex(ValueError, "topology must be"):
                validate_topology_request(request)

    def test_extreme_finite_cutoff_ranges_return_infeasible_report(self):
        request = json.loads((self.root / "docs/example-rc-highpass-request.json").read_text(encoding="utf-8"))
        for cutoff in (1e308, 5e-324):
            request["behavior"]["cutoff_hz"] = {"min": cutoff, "max": cutoff}
            with self.subTest(cutoff=cutoff), self.assertRaises(NoFeasibleCandidateError) as captured:
                synthesize_behavioral_request(request)
            self.assertEqual(captured.exception.report["status"], "infeasible")
            self.assertEqual(captured.exception.report["candidate_search"]["feasible_candidate_count"], 0)

    def test_schema_restricts_optional_fields_to_rc_lowpass(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema is not installed")
        schema = json.loads((self.root / "schemas/rc-lowpass-request.schema.json").read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)
        validator = jsonschema.Draft202012Validator(schema)
        highpass = json.loads((self.root / "docs/example-rc-highpass-request.json").read_text(encoding="utf-8"))
        rl = json.loads((self.root / "docs/example-rl-lowpass-request.json").read_text(encoding="utf-8"))
        lowpass = json.loads((self.root / "docs/example-rc-lowpass-loaded-request.json").read_text(encoding="utf-8"))
        self.assertTrue(validator.is_valid(highpass))
        self.assertTrue(validator.is_valid(rl))
        self.assertTrue(validator.is_valid(lowpass))
        highpass["behavior"]["passband_gain"] = {"min": 0.8, "max": 0.9}
        self.assertFalse(validator.is_valid(highpass))
        rl["operating_conditions"] = {"source_resistance_ohm": 0, "load_resistance_ohm": None}
        self.assertFalse(validator.is_valid(rl))
        series_rlc = json.loads((self.root / "docs/example-series-rlc-bandpass-request.json").read_text(encoding="utf-8"))
        self.assertTrue(validator.is_valid(series_rlc))
        series_rlc["behavior"]["passband_gain"] = {"min": 0.8, "max": 1.0}
        self.assertFalse(validator.is_valid(series_rlc))
        opamp_lp = json.loads((self.root / "docs/example-opamp-rc-lowpass-request.json").read_text(encoding="utf-8"))
        self.assertTrue(validator.is_valid(opamp_lp))
        opamp_lp["behavior"]["passband_gain"] = {"min": 0.8, "max": 1.0}
        self.assertFalse(validator.is_valid(opamp_lp))
        opamp_lp = json.loads((self.root / "docs/example-opamp-rc-lowpass-request.json").read_text(encoding="utf-8"))
        opamp_lp["operating_conditions"] = {"source_resistance_ohm": 0, "load_resistance_ohm": None}
        self.assertFalse(validator.is_valid(opamp_lp))
        inverting = json.loads((self.root / "docs/example-inverting-opamp-request.json").read_text(encoding="utf-8"))
        self.assertTrue(validator.is_valid(inverting))

    def test_series_rlc_validation_resonance_and_same_ir_artifacts(self):
        request = json.loads((self.root / "docs/example-series-rlc-bandpass-request.json").read_text(encoding="utf-8"))
        normalized = validate_topology_request(request)
        result = synthesize_behavioral_request(normalized)
        expected = 1.0 / (2.0 * math.pi * math.sqrt(0.1 * 1e-7))
        self.assertEqual(result.report["status"], "feasible")
        self.assertAlmostEqual(result.report["candidate"]["resonance_hz"], expected)
        self.assertIn("C1 in mid 1e-07", result.circuit_ir.render())
        self.assertIn("L1 mid out 0.1", result.circuit_ir.render())
        self.assertIn("R1 out 0 1000", result.circuit_ir.render())
        asc = render_asc(result.circuit_ir)
        for reference in ("C1", "L1", "R1"):
            self.assertIn(f"SYMATTR InstName {reference}", asc)
        self.assertEqual(result.report["verification_status"], "not_simulated")

    def test_series_rlc_broad_bounds_stream_only_capacitance_inductance_pairs(self):
        request = json.loads((self.root / "docs/example-series-rlc-bandpass-request.json").read_text(encoding="utf-8"))
        broad_bounds = {
            "resistance_ohm": {"min": 1e-6, "max": 1e12, "series": "E24"},
            "capacitance_f": {"min": 1e-15, "max": 1e3, "series": "E24"},
            "inductance_h": {"min": 1e-12, "max": 1e9, "series": "E24"},
        }
        request["components"] = broad_bounds
        result = synthesize_behavioral_request(request)
        search = result.report["candidate_search"]
        counts = search["available_component_values"]
        self.assertEqual(search["component_pairs_considered"], counts["capacitance_f"] * counts["inductance_h"])
        self.assertGreater(counts["resistance_ohm"] * search["component_pairs_considered"], 10_000_000)
        self.assertLess(search["component_pairs_considered"], 300_000)
        self.assertEqual(search["candidate_tuples_considered"],
                         search["component_pairs_considered"] * counts["resistance_ohm"])
        self.assertEqual(search["feasible_candidate_count"],
                         search["feasible_resonance_pair_count"] * counts["resistance_ohm"])
        self.assertEqual(result.report["candidate"]["resistance_ohm"], 1e-6)
        self.assertIn("lowest available E-series", search["resistance_selection"])

    def test_series_rlc_empty_resistance_values_returns_infeasible(self):
        request = json.loads((self.root / "docs/example-series-rlc-bandpass-request.json").read_text(encoding="utf-8"))
        request["components"]["resistance_ohm"]["min"] = 1.01
        request["components"]["resistance_ohm"]["max"] = 1.02
        with self.assertRaises(NoFeasibleCandidateError) as captured:
            synthesize_behavioral_request(request)
        report = captured.exception.report
        self.assertEqual(report["status"], "infeasible")
        self.assertEqual(report["candidate_search"]["component_pairs_considered"], 0)
        self.assertEqual(report["candidate_search"]["feasible_resonance_pair_count"], 0)
        self.assertIsNone(report["candidate"])

    def test_series_rlc_validation_rejects_cutoff_conditions_and_gain(self):
        example = self.root / "docs/example-series-rlc-bandpass-request.json"
        for mutation, message in (
            (lambda request: request["behavior"].update(cutoff_hz={"min": 100, "max": 200}), "resonance_hz"),
            (lambda request: request["behavior"].update(passband_gain={"min": 0.5, "max": 1.0}), "resonance_hz"),
            (lambda request: request.update(operating_conditions={"source_resistance_ohm": 0,
                                                                  "load_resistance_ohm": None}),
             "only schema_version"),
        ):
            request = json.loads(example.read_text(encoding="utf-8"))
            mutation(request)
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                validate_topology_request(request)

    def test_series_rlc_simulation_measures_output_peak_frequency(self):
        request = json.loads((self.root / "docs/example-series-rlc-bandpass-request.json").read_text(encoding="utf-8"))
        with TemporaryDirectory(dir=self.root) as directory:
            root = Path(directory)
            exe = root / "LTspice.exe"
            exe.write_text("fake", encoding="ascii")
            result, report = simulate_topology_request(
                request, root / "fresh-rlc-run", executable=str(exe), runner=_rlc_runner(1630.0))
            saved_ir = json.loads((root / "fresh-rlc-run/circuit-ir.json").read_text(encoding="utf-8"))
            self.assertEqual(saved_ir, result.circuit_ir.to_dict())
            self.assertTrue((root / "fresh-rlc-run/design.asc").is_file())
            self.assertIn("expected_resonance_hz", report["ac_sweep_hz"])
        self.assertEqual(report["verification_status"], "passed")
        self.assertAlmostEqual(report["measurements"]["resonance_hz"], 1630.0, delta=0.5)
        self.assertTrue(1500 <= report["measurements"]["resonance_hz"] <= 1700)
        self.assertEqual(report["measurements"]["ac_points"], 201)
        self.assertFalse(report["production_qualified"])

    def test_series_rlc_missing_raw_fails_closed(self):
        request = json.loads((self.root / "docs/example-series-rlc-bandpass-request.json").read_text(encoding="utf-8"))
        with TemporaryDirectory(dir=self.root) as directory:
            root = Path(directory)
            exe = root / "LTspice.exe"
            exe.write_text("fake", encoding="ascii")

            def no_raw_runner(command, cwd, capture_output, text, timeout, check):
                Path(command[-1]).with_suffix(".log").write_text("missing raw", encoding="ascii")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            _, report = simulate_topology_request(
                request, root / "missing-rlc-raw", executable=str(exe), runner=no_raw_runner)
        self.assertFalse(report["design_pass"])
        self.assertEqual(report["verification_status"], "failed")
        self.assertIsNone(report["measurements"]["resonance_hz"])
        self.assertFalse(report["hard_constraints"][0]["pass"])

    def test_rc_highpass_simulation_measures_ascending_crossing(self):
        request = json.loads((self.root / "docs/example-rc-highpass-request.json").read_text(encoding="utf-8"))
        with TemporaryDirectory(dir=self.root) as directory:
            root = Path(directory)
            exe = root / "LTspice.exe"
            exe.write_text("fake", encoding="ascii")
            result, report = simulate_topology_request(
                request, root / "fresh-highpass-run", executable=str(exe), runner=_highpass_runner())
            self.assertEqual(report["cutoff_direction"], "ascending")
            self.assertEqual(report["verification_status"], "passed")
            self.assertAlmostEqual(report["measurements"]["cutoff_hz"], 1.0 / (2 * math.pi * 1000 * 1e-7), delta=2.0)
            saved_ir = json.loads((root / "fresh-highpass-run/circuit-ir.json").read_text(encoding="utf-8"))
            self.assertEqual(saved_ir, result.circuit_ir.to_dict())
            self.assertTrue((root / "fresh-highpass-run/design.asc").is_file())

    def test_missing_ac_data_fails_closed_for_new_topology(self):
        request = json.loads((self.root / "docs/example-rc-highpass-request.json").read_text(encoding="utf-8"))
        with TemporaryDirectory(dir=self.root) as directory:
            root = Path(directory)
            exe = root / "LTspice.exe"
            exe.write_text("fake", encoding="ascii")

            def no_raw_runner(command, cwd, capture_output, text, timeout, check):
                Path(command[-1]).with_suffix(".log").write_text("no raw", encoding="ascii")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            _, report = simulate_topology_request(
                request, root / "missing-raw", executable=str(exe), runner=no_raw_runner)
        self.assertFalse(report["design_pass"])
        self.assertEqual(report["verification_status"], "failed")
        self.assertIsNone(report["measurements"]["cutoff_hz"])
        self.assertFalse(report["hard_constraints"][0]["pass"])


def _inverting_opamp_runner():
    def runner(command, cwd, capture_output, text, timeout, check):
        netlist = Path(command[-1])
        rows = []
        for index, frequency in enumerate((100.0, 1000.0, 10000.0)):
            rows.extend((f"{index} {frequency:.17g},0\n", " 1,0\n", " -10,0\n"))
        netlist.with_suffix(".raw").write_text(
            "Title: fake inverting opamp AC\nPlotname: AC Analysis\nNo. Variables: 3\nNo. Points: 3\n"
            "Variables:\n0 frequency frequency\n1 V(in) voltage\n2 V(out) voltage\nValues:\n" + "".join(rows),
            encoding="ascii",
        )
        netlist.with_suffix(".log").write_text("fake LTspice", encoding="ascii")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
    return runner


def _lowpass_runner():
    def runner(command, cwd, capture_output, text, timeout, check):
        netlist = Path(command[-1])
        raw_path = netlist.with_suffix(".raw")
        cutoff = 1.0 / (2 * math.pi * 1000 * 1e-7)
        rows = []
        for index in range(201):
            ratio = 0.1 * 10.0 ** (index / 100.0)
            frequency = cutoff * ratio
            output = 1.0 / complex(1.0, ratio)
            rows.extend((f"{index} {frequency:.17g},0\n", " 1,0\n",
                         f" {output.real:.17g},{output.imag:.17g}\n"))
        raw_path.write_text(
            "Title: fake lowpass\nPlotname: AC Analysis\nNo. Variables: 3\nNo. Points: 201\n"
            "Variables:\n0 frequency frequency\n1 V(in) voltage\n2 V(out) voltage\nValues:\n" + "".join(rows),
            encoding="ascii",
        )
        netlist.with_suffix(".log").write_text("fake LTspice", encoding="ascii")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
    return runner


def _highpass_runner():
    def runner(command, cwd, capture_output, text, timeout, check):
        netlist = Path(command[-1])
        raw_path = netlist.with_suffix(".raw")
        log_path = netlist.with_suffix(".log")
        cutoff = 1.0 / (2 * math.pi * 1000 * 1e-7)
        rows = []
        for index in range(201):
            ratio = 0.1 * 10.0 ** (index / 100.0)
            frequency = cutoff * ratio
            output = complex(0, ratio) / complex(1, ratio)
            rows.extend((f"{index} {frequency:.17g},0\n", " 1,0\n",
                         f" {output.real:.17g},{output.imag:.17g}\n"))
        raw_path.write_text(
            "Title: fake highpass\nPlotname: AC Analysis\nNo. Variables: 3\nNo. Points: 201\n"
            "Variables:\n0 frequency frequency\n1 V(in) voltage\n2 V(out) voltage\nValues:\n" + "".join(rows),
            encoding="ascii",
        )
        log_path.write_text("fake LTspice", encoding="ascii")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
    return runner


def _rlc_runner(measured_resonance_hz: float):
    def runner(command, cwd, capture_output, text, timeout, check):
        netlist = Path(command[-1])
        rows = []
        for index in range(201):
            ratio = 0.1 * 10.0 ** (index / 100.0)
            frequency = measured_resonance_hz * ratio
            response = 1.0 / complex(1.0, 2.0 * (ratio - 1.0 / ratio))
            rows.extend((f"{index} {frequency:.17g},0\n", " 1,0\n",
                         f" {response.real:.17g},{response.imag:.17g}\n"))
        netlist.with_suffix(".raw").write_text(
            "Title: fake RLC AC\nPlotname: AC Analysis\nNo. Variables: 3\nNo. Points: 201\n"
            "Variables:\n0 frequency frequency\n1 V(in) voltage\n2 V(out) voltage\nValues:\n" + "".join(rows),
            encoding="ascii",
        )
        netlist.with_suffix(".log").write_text("fake LTspice", encoding="ascii")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
    return runner


if __name__ == "__main__":
    unittest.main()
