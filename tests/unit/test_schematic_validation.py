import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from circuitpilot.designs import EXAMPLE_DESIGNS
from circuitpilot.schematic_validation import read_native_netlist, validate_native_netlist


class NativeNetlistValidationTests(unittest.TestCase):
    def test_accepts_ir_equivalent_native_netlist_with_continuations_and_case_changes(self):
        design = EXAMPLE_DESIGNS["inverting-opamp-amplifier"]
        report = validate_native_netlist(design.ir, design.ir.render().upper().replace("XU1 0 SUMMING OUT CP_OPAMP", "XU1 0 SUMMING OUT CP_OPAMP"))
        self.assertTrue(report["all_match"])

    def test_rejects_missing_extra_pin_or_changed_value(self):
        design = EXAMPLE_DESIGNS["mosfet-bias-network"]
        source = design.ir.render()
        self.assertFalse(validate_native_netlist(design.ir, source.replace("RD vdd drain 1k", "RD vdd drain 2k"))["all_match"])
        self.assertFalse(validate_native_netlist(design.ir, source.replace("M1 drain gate source source", "M1 drain gate 0 source"))["all_match"])
        self.assertFalse(validate_native_netlist(design.ir, source.replace("RS source 0 470\n", ""))["all_match"])
        self.assertFalse(validate_native_netlist(design.ir, source.replace(".end", "R_EXTRA out 0 1k\n.end"))["all_match"])

    def test_joins_native_continuation_values(self):
        design = EXAMPLE_DESIGNS["common-source-mosfet-amplifier"]
        source = design.ir.render().replace(
            "M1 out gate source source CP_NMOS W=10u L=1u",
            "M1 out gate source source CP_NMOS W=10u\n+ L=1u",
        )
        report = validate_native_netlist(design.ir, source)
        self.assertTrue(report["all_match"])

    def test_accepts_ltspice_micro_suffix_and_unused_default_models(self):
        design = EXAMPLE_DESIGNS["common-source-mosfet-amplifier"]
        source = design.ir.render().replace("CIN in gate 10u", "CIN in gate 10µ")
        source = source.replace("CS source 0 100u", "CS source 0 100µ")
        source = source.replace(
            ".model CP_NMOS NMOS(VTO=2 KP=10m LAMBDA=0.02 CGSO=10n CGDO=5n)",
            ".model CP_NMOS NMOS(VTO=2 KP=10m LAMBDA=0.02 CGSO=10n CGDO=5n)\n"
            ".model NMOS NMOS\n.model PMOS PMOS",
        )

        report = validate_native_netlist(design.ir, source)

        self.assertTrue(report["all_match"], report)
        self.assertTrue(report["component_checks"]["cin"]["value_match"])
        self.assertTrue(report["component_checks"]["cs"]["value_match"])
        self.assertEqual(
            report["definition_checks"]["models"]["ignored_unreferenced_defaults"],
            [".model nmos nmos", ".model pmos pmos"],
        )

    def test_rejects_changed_model_and_does_not_ignore_a_referenced_default(self):
        design = EXAMPLE_DESIGNS["common-source-mosfet-amplifier"]
        source = design.ir.render().replace("KP=10m", "KP=20m")
        self.assertFalse(validate_native_netlist(design.ir, source)["all_match"])

        source = design.ir.render().replace("CP_NMOS W=10u L=1u", "NMOS W=10u L=1u")
        source = source.replace(
            ".model CP_NMOS NMOS(VTO=2 KP=10m LAMBDA=0.02 CGSO=10n CGDO=5n)",
            ".model CP_NMOS NMOS(VTO=2 KP=10m LAMBDA=0.02 CGSO=10n CGDO=5n)\n.model NMOS NMOS",
        )
        report = validate_native_netlist(design.ir, source)
        self.assertFalse(report["all_match"])
        self.assertIn(".model nmos nmos", report["definition_checks"]["models"]["unexpected_in_native"])

    def test_reads_utf8_micro_units_without_lossy_replacement(self):
        with TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            netlist = Path(temp_dir) / "native.net"
            netlist.write_bytes("CIN in gate 10µ\n".encode("utf-8"))
            self.assertEqual(read_native_netlist(netlist), "CIN in gate 10µ\n")

    def test_reads_utf16_ltspice_netlist(self):
        with TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            netlist = Path(temp_dir) / "native.net"
            netlist.write_bytes("CIN in gate 10µ\n".encode("utf-16"))
            self.assertEqual(read_native_netlist(netlist), "CIN in gate 10µ\n")

    def test_accepts_ltspice_grounded_bjt_substrate_and_unused_defaults(self):
        design = EXAMPLE_DESIGNS["common-emitter-bjt-amplifier"]
        source = design.ir.render().replace(
            "Q1 out base emitter CP_NPN", "Q1 out base emitter 0 CP_NPN"
        )
        source = source.replace(
            ".model CP_NPN NPN(IS=1e-14 BF=150 VAF=100 CJE=10p CJC=5p TF=0.3n)",
            ".model CP_NPN NPN(IS=1e-14 BF=150 VAF=100 CJE=10p CJC=5p TF=0.3n)\n"
            ".model NPN NPN\n.model PNP PNP",
        )

        report = validate_native_netlist(design.ir, source)

        self.assertTrue(report["all_match"], report)
        self.assertTrue(report["component_checks"]["q1"]["pins_match"])
        self.assertTrue(report["component_checks"]["q1"]["value_match"])
        self.assertEqual(
            report["definition_checks"]["models"]["ignored_unreferenced_defaults"],
            [".model npn npn", ".model pnp pnp"],
        )

    def test_rejects_non_ground_bjt_substrate_changed_pins_or_model(self):
        design = EXAMPLE_DESIGNS["common-emitter-bjt-amplifier"]
        source = design.ir.render().replace(
            "Q1 out base emitter CP_NPN", "Q1 out base emitter substrate CP_NPN"
        )
        self.assertFalse(validate_native_netlist(design.ir, source)["all_match"])

        source = design.ir.render().replace(
            "Q1 out base emitter CP_NPN", "Q1 collector base emitter 0 CP_NPN"
        )
        self.assertFalse(validate_native_netlist(design.ir, source)["all_match"])

        source = design.ir.render().replace(
            "Q1 out base emitter CP_NPN", "Q1 out base emitter 0 CP_OTHER"
        )
        self.assertFalse(validate_native_netlist(design.ir, source)["all_match"])

        source = design.ir.render().replace("BF=150", "BF=151")
        self.assertFalse(validate_native_netlist(design.ir, source)["all_match"])

    def test_accepts_ltspice_opamp_instance_metadata_for_matching_subcircuit(self):
        design = EXAMPLE_DESIGNS["inverting-opamp-amplifier"]
        source = design.ir.render().replace(
            "XU1 0 summing out CP_OPAMP",
            "X§XU1 0 summing out CP_OPAMP ;§pnba INP)INM)OUT",
        )

        report = validate_native_netlist(design.ir, source)

        self.assertTrue(report["all_match"], report)
        self.assertTrue(report["component_checks"]["xu1"]["pins_match"])
        self.assertTrue(report["component_checks"]["xu1"]["value_match"])

    def test_ltspice_opamp_metadata_does_not_hide_changed_pins_or_subcircuit(self):
        design = EXAMPLE_DESIGNS["inverting-opamp-amplifier"]
        native_instance = "X§XU1 0 summing out CP_OPAMP ;§pnba INP)INM)OUT"
        changed_pin = design.ir.render().replace(
            "XU1 0 summing out CP_OPAMP",
            "X§XU1 0 summing inverting_input CP_OPAMP ;§pnba INP)INM)OUT",
        )
        self.assertFalse(validate_native_netlist(design.ir, changed_pin)["all_match"])

        changed_subcircuit = design.ir.render().replace(
            "XU1 0 summing out CP_OPAMP", native_instance.replace("CP_OPAMP", "CP_OTHER")
        )
        self.assertFalse(validate_native_netlist(design.ir, changed_subcircuit)["all_match"])


if __name__ == "__main__":
    unittest.main()
