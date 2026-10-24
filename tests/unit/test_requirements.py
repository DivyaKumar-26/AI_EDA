import unittest
from pathlib import Path

from circuitpilot.requirements import evaluate_requirements, load_spec, validate_spec


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


class RequirementsTests(unittest.TestCase):
    def test_validates_supported_metric_and_unit(self):
        self.assertEqual(validate_spec(_spec())["design_id"], "inverting-opamp-amplifier")

    def test_documented_example_spec_loads(self):
        project_root = Path(__file__).resolve().parents[2]
        loaded = load_spec(project_root / "docs" / "example-inverting-opamp-spec.json")
        self.assertEqual(loaded["design_id"], "inverting-opamp-amplifier")

    def test_rejects_unknown_fields_metric_and_wrong_units(self):
        spec = _spec()
        spec["extra"] = True
        with self.assertRaisesRegex(ValueError, "unsupported field"):
            validate_spec(spec)
        spec = _spec()
        spec["requirements"][0]["metric"] = "arbitrary.python.expression"
        with self.assertRaisesRegex(ValueError, "unsupported metric"):
            validate_spec(spec)
        spec = _spec()
        spec["requirements"][0]["unit"] = "Hz"
        with self.assertRaisesRegex(ValueError, "requires unit"):
            validate_spec(spec)

    def test_rejects_nonfinite_or_invalid_ranges(self):
        spec = _spec()
        spec["requirements"][0]["min"] = float("nan")
        with self.assertRaisesRegex(ValueError, "finite"):
            validate_spec(spec)
        spec = _spec()
        spec["requirements"][0]["min"] = 11
        with self.assertRaisesRegex(ValueError, "less than or equal"):
            validate_spec(spec)
        spec = _spec()
        spec["operating_conditions"]["ambient_temperature_c"] = [70, 27, 0]
        with self.assertRaisesRegex(ValueError, "ordered from low to high"):
            validate_spec(spec)

    def test_evaluation_is_inclusive_and_fails_closed_on_missing_or_nonfinite(self):
        spec = _spec()
        exact = evaluate_requirements(spec, {"gain_at_1khz_magnitude_v_per_v": 9.5})
        self.assertEqual(exact["status"], "passed")
        missing = evaluate_requirements(spec, {})
        self.assertEqual(missing["status"], "failed")
        self.assertEqual(missing["checks"][0]["failure"], "measurement missing or non-finite")
        infinite = evaluate_requirements(spec, {"gain_at_1khz_magnitude_v_per_v": float("inf")})
        self.assertEqual(infinite["status"], "failed")


if __name__ == "__main__":
    unittest.main()
