import unittest

from circuitpilot.designs import EXAMPLE_DESIGNS, TOPOLOGY_REGISTRY
from circuitpilot.ir import CircuitElement, CircuitIR
from circuitpilot.rawdata import parse_ascii_raw


class ExampleDesignTests(unittest.TestCase):
    def test_catalog_has_all_six_deterministic_netlists_and_boms(self):
        expected = {
            "common-source-mosfet-amplifier",
            "common-emitter-bjt-amplifier",
            "inverting-opamp-amplifier",
            "non-inverting-opamp-amplifier",
            "mosfet-bias-network",
            "bjt-bias-network",
        }
        self.assertEqual(set(EXAMPLE_DESIGNS), expected)
        for identifier, design in EXAMPLE_DESIGNS.items():
            with self.subTest(design=identifier):
                self.assertTrue(design.netlist.endswith(".end\n"))
                self.assertEqual(design.netlist, EXAMPLE_DESIGNS[identifier].netlist)
                self.assertEqual(design.ir.render(), design.netlist)
                self.assertGreater(len(design.bom), 0)
                self.assertNotIn(".include", design.netlist.casefold())

    def test_op_raw_parser_reads_scalar_operating_point(self):
        raw = """Title: sample
Plotname: Operating Point
Flags: real
No. Variables: 3
No. Points: 1
Variables:
\t0\tV(base)\tvoltage
\t1\tV(emitter)\tvoltage
\t2\tV(collector)\tvoltage
Values:
0\t2.1000000000000000e+00
\t1.4000000000000000e+00
\t6.0000000000000000e+00
"""
        plot = parse_ascii_raw(raw)
        self.assertEqual(plot.plot_name, "Operating Point")
        self.assertAlmostEqual(plot.points[0]["v(base)"].real, 2.1)
        self.assertAlmostEqual(plot.points[0]["v(emitter)"].real, 1.4)
        self.assertAlmostEqual(plot.points[0]["v(collector)"].real, 6.0)

    def test_topology_registry_validates_rc_parameters_and_exposes_schema(self):
        ir = TOPOLOGY_REGISTRY.synthesize("rc_lowpass", {"resistance_ohm": 1_000, "capacitance_f": 100e-9})
        self.assertIn("R1 in out 1000", ir.render())
        schema = TOPOLOGY_REGISTRY.schema_document()
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        with self.assertRaisesRegex(ValueError, "unsupported topology parameter"):
            TOPOLOGY_REGISTRY.synthesize("rc_lowpass", {"resistance_ohm": 1_000, "capacitance_f": 100e-9, "raw": ".include x"})
        with self.assertRaisesRegex(ValueError, "minimum"):
            TOPOLOGY_REGISTRY.synthesize("rc_lowpass", {"resistance_ohm": 0, "capacitance_f": 100e-9})

    def test_ir_rejects_duplicate_component_references(self):
        with self.assertRaisesRegex(ValueError, "must be unique"):
            CircuitIR("duplicate", (CircuitElement.from_statement("R1 in out 1k"), CircuitElement.from_statement("r1 out 0 1k")))


if __name__ == "__main__":
    unittest.main()
