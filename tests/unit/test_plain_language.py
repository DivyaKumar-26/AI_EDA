import json
from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from circuitpilot import design_request_cli
from circuitpilot.plain_language import parse_plain_language_request
from circuitpilot.topology_requests import synthesize_behavioral_request


class PlainLanguageRequestTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[2]
        self.prompt_path = self.root / "docs" / "example-rc-lowpass-prompt.txt"
        self.request_path = self.root / "docs" / "example-rc-lowpass-request.json"
        self.prompt = self.prompt_path.read_text(encoding="utf-8").strip()
        self.request = json.loads(self.request_path.read_text(encoding="utf-8"))

    def _run_cli(self, args):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = design_request_cli.main(args)
        return exit_code, json.loads(stdout.getvalue())

    def test_documented_prompt_normalizes_and_synthesizes(self):
        intake = parse_plain_language_request(self.prompt)
        self.assertEqual(intake["status"], "complete")
        self.assertEqual(intake["request"], self.request)
        self.assertEqual(intake["assumptions"], [
            "Uses the existing canonical ideal-source, open-load RC low-pass model."
        ])

        result = synthesize_behavioral_request(intake["request"])
        self.assertEqual(result.report["status"], "feasible")
        self.assertEqual(result.report["candidate"]["resistance_ohm"], 1000.0)
        self.assertEqual(result.report["candidate"]["capacitance_f"], 1e-7)
        self.assertIn("R1 in out 1000", result.circuit_ir.render())

    def test_sentence_prompts_cover_existing_behavioral_topologies(self):
        prompts = {
            "rc_lowpass": "Design an RC low-pass filter with cutoff from 1.5 kHz to 1.7 kHz, resistance from 1 kOhm to 10 kOhm using E12, and capacitance from 50 nF to 150 nF using E12.",
            "rc_highpass": "Design an RC high-pass filter with cutoff between 1.5 kHz and 1.7 kHz, resistance between 1 kOhm and 10 kOhm using E12, and capacitance between 50 nF and 150 nF using E12.",
            "rl_lowpass": "Design an RL low-pass filter with cutoff from 1.5 kHz to 1.7 kHz, resistance from 1 kOhm to 10 kOhm using E12, and inductance from 50 mH to 150 mH using E12.",
            "opamp_rc_lowpass": "Design an op-amp buffered RC low-pass filter with cutoff from 1.5 kHz to 1.7 kHz, resistance from 1 kOhm to 10 kOhm using E12, and capacitance from 50 nF to 150 nF using E12.",
            "series_rlc_bandpass": "Design a series RLC band-pass filter with resonance from 1.5 kHz to 1.7 kHz, resistance from 100 Ohm to 1 kOhm using E12, capacitance from 50 nF to 150 nF using E12, and inductance from 50 mH to 150 mH using E12.",
            "inverting_opamp_amplifier": "Design an inverting op-amp amplifier with gain magnitude from 2 V/V to 10 V/V, input resistance from 1 kOhm to 10 kOhm using E12, and feedback resistance from 2 kOhm to 100 kOhm using E12.",
        }
        for expected_topology, prompt in prompts.items():
            with self.subTest(topology=expected_topology):
                intake = parse_plain_language_request(prompt)
                self.assertEqual(intake["status"], "complete", intake)
                self.assertEqual(intake["request"]["topology"], expected_topology)
                result = synthesize_behavioral_request(intake["request"])
                self.assertEqual(result.report["status"], "feasible")

    def test_missing_fields_return_clarification_questions(self):
        intake = parse_plain_language_request(
            "RC low-pass; cutoff: 1.5 kHz to 1.7 kHz; resistance: 1 kOhm to 10 kOhm E12"
        )
        self.assertEqual(intake["status"], "needs_clarification")
        self.assertEqual(intake["missing_fields"], ["capacitance"])
        self.assertEqual(len(intake["clarification_questions"]), 1)
        self.assertIn("capacitance", intake["clarification_questions"][0])

    def test_sentence_missing_fields_ambiguity_and_unsupported_conditions(self):
        missing = parse_plain_language_request(
            "Design an RL low-pass filter with cutoff from 1.5 kHz to 1.7 kHz and resistance from 1 kOhm to 10 kOhm using E12."
        )
        self.assertEqual(missing["status"], "needs_clarification")
        self.assertIn("components.inductance_h", missing["missing_fields"])

        ambiguous = parse_plain_language_request(
            "Design an RC low-pass filter with cutoff from 1.5 kHz to 1.7 kHz and cutoff from 2 kHz to 3 kHz, "
            "resistance from 1 kOhm to 10 kOhm using E12, and capacitance from 50 nF to 150 nF using E12."
        )
        self.assertEqual(ambiguous["status"], "needs_clarification")
        self.assertIn("cutoff_hz", ambiguous["missing_fields"])

        unsupported = parse_plain_language_request(
            "Design an RC low-pass filter with cutoff from 1.5 kHz to 1.7 kHz, source resistance 100 Ohm, "
            "resistance from 1 kOhm to 10 kOhm using E12, and capacitance from 50 nF to 150 nF using E12."
        )
        self.assertEqual(unsupported["status"], "unsupported")

    def test_sentence_rejects_an_extra_behavior_requirement(self):
        prompt = (
            "Design an RC low-pass filter with cutoff from 1.5 kHz to 1.7 kHz and gain magnitude "
            "from 2 V/V to 10 V/V, resistance from 1 kOhm to 10 kOhm using E12, and "
            "capacitance from 50 nF to 150 nF using E12."
        )
        intake = parse_plain_language_request(prompt)
        self.assertEqual(intake["status"], "unsupported")
        self.assertIn("gain", [term.lower() for term in intake["unsupported_phrasing"]])

    def test_legacy_labeled_syntax_rejects_duplicate_fields(self):
        duplicates = {
            "cutoff": "cutoff: 1.6 kHz to 1.7 kHz",
            "target": "target: 1.6 kHz",
            "resistance": "resistance: 2 kOhm to 8 kOhm E12",
            "capacitance": "capacitance: 60 nF to 120 nF E12",
        }
        for field, duplicate_clause in duplicates.items():
            with self.subTest(field=field):
                intake = parse_plain_language_request(f"{self.prompt}; {duplicate_clause}")
                self.assertEqual(intake["status"], "needs_clarification")
                self.assertIn(field, intake["missing_fields"])

    def test_malformed_component_and_general_prose_are_not_guessed(self):
        malformed = parse_plain_language_request(
            "RC low-pass; cutoff: 1.5 kHz to 1.7 kHz; resistance: 1 kOhm to 10 kOhm; "
            "capacitance: 10 nF to 1 uF E12"
        )
        self.assertEqual(malformed["status"], "needs_clarification")
        self.assertIn("resistance", malformed["missing_fields"])

        unsupported = parse_plain_language_request(
            "Please design a low pass for audio with sensible resistor and capacitor values."
        )
        self.assertEqual(unsupported["status"], "unsupported")
        self.assertIn("unsupported_phrasing", unsupported)

    def test_extreme_decimal_exponent_returns_invalid_request_in_parser_and_cli(self):
        extreme_prompt = self.prompt.replace(
            "cutoff: 1.5 kHz to 1.7 kHz", "cutoff: 1e9999999 Hz to 1.7 kHz"
        )
        intake = parse_plain_language_request(extreme_prompt)
        self.assertEqual(intake["status"], "invalid_request")
        self.assertIn("finite positive quantity", intake["message"])

        exit_code, output = self._run_cli(["--prompt", extreme_prompt])
        self.assertEqual(exit_code, 2)
        self.assertEqual(output["intake_report"]["status"], "invalid_request")

    def test_cli_prompt_and_existing_request_option_route_through_validated_synthesis(self):
        natural_prompt = (
            "Design an RC high-pass filter with cutoff from 1.5 kHz to 1.7 kHz, resistance from "
            "1 kOhm to 10 kOhm using E12, and capacitance from 50 nF to 150 nF using E12."
        )
        with patch.object(design_request_cli, "synthesize_behavioral_request",
                          wraps=design_request_cli.synthesize_behavioral_request) as synthesize:
            prompt_code, prompt_output = self._run_cli(["--prompt", self.prompt])
            request_code, request_output = self._run_cli(["--request", str(self.request_path)])
            natural_code, natural_output = self._run_cli(["--prompt", natural_prompt])

        self.assertEqual(prompt_code, 0)
        self.assertEqual(request_code, 0)
        self.assertEqual(natural_code, 0)
        self.assertEqual(prompt_output["normalized_request"], self.request)
        self.assertEqual(prompt_output["input_mode"], "plain_language")
        self.assertEqual(request_output["synthesis_report"]["status"], "feasible")
        self.assertEqual(natural_output["normalized_request"]["topology"], "rc_highpass")
        self.assertEqual(synthesize.call_count, 3)

    def test_cli_prompt_remains_usable_with_mocked_simulation_and_refinement(self):
        synthesis = synthesize_behavioral_request(self.request)
        simulation = SimpleNamespace(
            synthesis_result=synthesis,
            report={"design_pass": True, "verification_status": "passed"},
        )
        refinement = SimpleNamespace(
            report={"design_pass": True, "status": "passed"},
            history={"attempts": []},
            selected_result=synthesis,
            selected_verification={"design_pass": True},
        )
        with patch.object(design_request_cli, "simulate_rc_request", return_value=simulation) as simulate:
            sim_code, sim_output = self._run_cli([
                "--prompt", self.prompt, "--simulate", "--output-dir", "unused-fresh-path"
            ])
        self.assertEqual(sim_code, 0)
        self.assertEqual(sim_output["normalized_request"], self.request)
        self.assertEqual(simulate.call_args.args[0], self.request)

        with patch.object(design_request_cli, "refine_rc_request", return_value=refinement) as refine:
            refine_code, refine_output = self._run_cli([
                "--prompt", self.prompt, "--simulate", "--refine", "--budget", "2",
                "--output-dir", "unused-fresh-path",
            ])
        self.assertEqual(refine_code, 0)
        self.assertEqual(refine_output["normalized_request"], self.request)
        self.assertEqual(refine.call_args.args[0], self.request)
        self.assertEqual(refine.call_args.args[2], 2)

    def test_cli_requires_exactly_one_input_mode_and_reports_incomplete_prompt(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit) as caught:
            design_request_cli.main(["--request", str(self.request_path), "--prompt", self.prompt])
        self.assertEqual(caught.exception.code, 2)

        exit_code, output = self._run_cli([
            "--prompt", "RC low-pass; cutoff: 1.5 kHz to 1.7 kHz"
        ])
        self.assertEqual(exit_code, 2)
        self.assertEqual(output["intake_report"]["status"], "needs_clarification")
        self.assertIn("clarification_questions", output["intake_report"])

    def test_cli_help_describes_sentence_topology_grammar(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout), self.assertRaises(SystemExit) as caught:
            design_request_cli.main(["--help"])
        self.assertEqual(caught.exception.code, 0)
        help_text = " ".join(stdout.getvalue().split())
        self.assertIn("sentence grammar for supported behavioral topologies", help_text)
        self.assertIn("docs/plain-language-request.md", help_text)


if __name__ == "__main__":
    unittest.main()
