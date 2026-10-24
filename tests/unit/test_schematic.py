import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import sys

from circuitpilot.designs import EXAMPLE_DESIGNS
from circuitpilot.schematic import (
    SYMBOLS,
    connectivity_manifest,
    connectivity_pin_map,
    render_asc,
    required_support_files,
)
from circuitpilot.schematics_cli import _compare_measurements, _verification_fields, export_schematics, main


class SchematicExporterTests(unittest.TestCase):
    def test_all_six_schematics_render_ir_connectivity_and_analysis_directives(self):
        self.assertEqual(len(EXAMPLE_DESIGNS), 6)
        for design in EXAMPLE_DESIGNS.values():
            with self.subTest(design=design.design_id):
                asc = render_asc(design.ir, design.title)
                manifest = connectivity_manifest(design.ir)
                pin_map = connectivity_pin_map(design.ir)
                for element in design.ir.elements:
                    self.assertEqual(manifest[element.reference], element.nodes)
                    self.assertIn(f"SYMATTR InstName {element.reference}", asc)
                    spec = SYMBOLS[element.reference[0].upper()]
                    symbol_nodes = element.nodes[:len(spec.pin_offsets)]
                    self.assertEqual([pin["pin_name"] for pin in pin_map[element.reference]["pins"]], list(spec.pin_names))
                    self.assertEqual([pin["net"] for pin in pin_map[element.reference]["pins"]], list(symbol_nodes))
                    for node in symbol_nodes:
                        self.assertRegex(asc, rf"(?m)^FLAG \d+ \d+ {node}$")
                    self.assertIn("WIRE ", asc)
                for directive in design.ir.directives:
                    self.assertIn(f"!{directive}", asc)
                for model in design.ir.models:
                    self.assertIn(f"!{model}", asc)

    def test_opamp_subcircuit_and_symbol_are_included_as_editable_support(self):
        design = EXAMPLE_DESIGNS["inverting-opamp-amplifier"]
        support = required_support_files(design.ir)
        self.assertIn("CP_OPAMP.asy", support)
        asc = render_asc(design.ir)
        self.assertIn("SYMBOL CP_OPAMP", asc)
        for line in design.ir.subcircuits[0].splitlines():
            self.assertIn(f"!{line}", asc)
        self.assertIn("SpiceOrder 3", support["CP_OPAMP.asy"])
        mapping = connectivity_pin_map(design.ir)["XU1"]["pins"]
        self.assertEqual([(pin["spice_order"], pin["pin_name"]) for pin in mapping],
                         [(1, "INP"), (2, "INM"), (3, "OUT")])

    def test_declared_builtin_pin_mappings_match_installed_ltspice_symbols_when_available(self):
        symbol_root = Path.home() / "AppData" / "Local" / "LTspice" / "lib" / "sym"
        if not symbol_root.is_dir():
            self.skipTest("LTspice symbol library is not installed")
        for spec in SYMBOLS.values():
            if spec.name == "CP_OPAMP":
                continue
            candidates = list(symbol_root.rglob(f"{spec.name}.asy"))
            if not candidates:
                self.skipTest(f"LTspice symbol {spec.name}.asy is not installed")
            pins = {}
            current = None
            for line in candidates[0].read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("PIN "):
                    tokens = line.split()
                    current = {"offset": (int(tokens[1]), int(tokens[2]))}
                elif current is not None and line.startswith("PINATTR PinName "):
                    current["name"] = line.split(None, 2)[2]
                elif current is not None and line.startswith("PINATTR SpiceOrder "):
                    current["order"] = int(line.rsplit(" ", 1)[1])
                    pins[current["order"]] = current
                    current = None
            ordered = [pins[index] for index in range(1, len(spec.pin_names) + 1)]
            self.assertEqual(tuple(pin["offset"] for pin in ordered), spec.pin_offsets, spec.name)
            self.assertEqual(tuple(pin["name"] for pin in ordered), spec.pin_names, spec.name)

    def test_export_writes_editable_artifacts_and_refuses_existing_folders(self):
        with TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            output = Path(temp_dir) / "exports"
            reports = export_schematics(output)
            self.assertEqual(len(reports), 6)
            for design in EXAMPLE_DESIGNS.values():
                folder = output / design.design_id
                self.assertTrue((folder / f"{design.design_id}.asc").is_file())
                self.assertEqual((folder / f"{design.design_id}.cir").read_text(encoding="ascii"), design.ir.render())
                report = json.loads((folder / "schematic-export-report.json").read_text(encoding="utf-8"))
                self.assertEqual(report["simulation_status"], "not_run")
                manifest = json.loads((folder / "connectivity-manifest.json").read_text(encoding="utf-8"))
                self.assertEqual(manifest["ir_nodes_by_reference"], report["connectivity"])
            self.assertTrue((output / "inverting-opamp-amplifier" / "CP_OPAMP.asy").is_file())
            first_asc = output / "common-source-mosfet-amplifier" / "common-source-mosfet-amplifier.asc"
            original = first_asc.read_bytes()
            with self.assertRaises(FileExistsError):
                export_schematics(output)
            self.assertEqual(first_asc.read_bytes(), original)

    def test_cir_comparison_distinguishes_pass_and_mismatch(self):
        reference = {"gain": 10.0, "checks": {"bias_ok": True}}
        self.assertTrue(_compare_measurements(reference, {"gain": 10.01, "checks": {"bias_ok": True}})["all_match"])
        passed = _verification_fields({"all_match": True})
        self.assertEqual(passed, {"verification_status": "passed", "design_pass": True})
        mismatch = _compare_measurements(reference, {"gain": 12.0, "checks": {"bias_ok": False}})
        self.assertFalse(mismatch["all_match"])
        failed = _verification_fields(mismatch)
        self.assertEqual(failed, {"verification_status": "failed", "design_pass": False})
        self.assertFalse(mismatch["checks"]["gain"]["match"])
        self.assertFalse(mismatch["checks"]["checks.bias_ok"]["match"])

    def test_cli_exits_nonzero_for_mismatched_comparison_and_zero_for_pass(self):
        for comparison_passes, expected_exit in ((True, 0), (False, 1)):
            with self.subTest(comparison_passes=comparison_passes), TemporaryDirectory(dir=Path.cwd()) as temp_dir:
                report_path = Path(temp_dir) / "report.json"
                report_path.write_text(json.dumps({
                    "design_id": "test-design",
                    "comparison_to_cir_run": {"all_match": comparison_passes},
                }), encoding="utf-8")
                with patch.object(sys, "argv", ["schematics_cli"]), \
                        patch("circuitpilot.schematics_cli.export_and_simulate", return_value=[report_path]):
                    self.assertEqual(main(), expected_exit)


if __name__ == "__main__":
    unittest.main()
