"""Export and batch-simulate the example designs as editable LTspice schematics."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import subprocess

from .design_runner import _amplifier_measurements, _bias_measurements
from .designs import EXAMPLE_DESIGNS
from .rawdata import parse_ascii_raw
from .schematic import connectivity_manifest, connectivity_pin_map, render_asc, required_support_files
from .simulation import SimulationError, discover_ltspice


def export_schematics(output_root: str | Path, design_ids: list[str] | None = None) -> list[Path]:
    """Write ASC, canonical CIR, BOM, local support symbols, and connectivity data."""
    root = Path(output_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    selected = _select_designs(design_ids)
    reports = []
    for design in selected:
        folder = root / design.design_id
        folder.mkdir()
        asc_path, cir_path, bom_path, support = _write_design_files(design, folder)
        connectivity = {reference: list(nodes) for reference, nodes in connectivity_manifest(design.ir).items()}
        pin_mapping = connectivity_pin_map(design.ir)
        (folder / "connectivity-manifest.json").write_text(json.dumps(
            {"ir_nodes_by_reference": connectivity, "symbol_pin_mapping": pin_mapping}, indent=2
        ) + "\n", encoding="utf-8")
        report_path = folder / "schematic-export-report.json"
        report_path.write_text(json.dumps({
            "design_id": design.design_id,
            "export_status": "completed",
            "simulation_status": "not_run",
            "schematic": asc_path.name,
            "canonical_netlist": cir_path.name,
            "bom": bom_path.name,
            "support_symbols": sorted(support),
            "connectivity_source": "CircuitIR element pins, mapped by LTspice symbol SpiceOrder",
            "connectivity": connectivity,
            "symbol_pin_mapping": pin_mapping,
            "simulation_note": "ASC batch simulation was unavailable in the current environment; this is a structural export, not ASC simulation verification.",
        }, indent=2) + "\n", encoding="utf-8")
        reports.append(report_path)
    return reports


def export_and_simulate(output_root: str | Path, executable: str | None = None, timeout_seconds: float = 240.0,
                        reference_root: str | Path = "output/analog_examples_v2",
                        design_ids: list[str] | None = None) -> list[Path]:
    exe = discover_ltspice(executable)
    root = Path(output_root).expanduser().resolve()
    reference = Path(reference_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    reports: list[Path] = []
    selected = _select_designs(design_ids)
    for design in selected:
        folder = root / design.design_id
        folder.mkdir()  # do not replace earlier artifacts
        asc_path, _, _, _ = _write_design_files(design, folder)

        try:
            completed = subprocess.run([str(exe), "-ascii", "-b", str(asc_path)], cwd=str(folder),
                                       capture_output=True, text=True, timeout=timeout_seconds, check=False)
        except subprocess.TimeoutExpired as exc:
            raise SimulationError(f"LTspice timed out on schematic {asc_path}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise SimulationError(f"LTspice rejected {asc_path.name} (exit {completed.returncode}): {detail}")

        raw_path = folder / f"{design.design_id}.raw"
        log_path = folder / f"{design.design_id}.log"
        net_path = folder / f"{design.design_id}.net"
        if not raw_path.is_file() or not log_path.is_file():
            raise SimulationError(f"LTspice did not produce raw and log outputs for {asc_path}")
        raw_plot = parse_ascii_raw(raw_path.read_text(encoding="ascii", errors="replace"))
        op_raw_path = folder / f"{design.design_id}.op.raw"
        op_plot = parse_ascii_raw(op_raw_path.read_text(encoding="ascii", errors="replace")) if op_raw_path.is_file() else None
        if design.measurement_kind.endswith("amplifier"):
            measurements = _amplifier_measurements(design, raw_plot, op_plot)
        else:
            measurements = _bias_measurements(design, raw_plot)

        ref_path = reference / design.design_id / "verification-report.json"
        if not ref_path.is_file():
            raise SimulationError(f"Reference CIR report is missing: {ref_path}")
        reference_report = json.loads(ref_path.read_text(encoding="utf-8"))
        comparison = _compare_measurements(reference_report["measurements"], measurements)
        report = {
            "design_id": design.design_id,
            "simulation_status": "completed",
            **_verification_fields(comparison),
            "simulator": {"name": "LTspice", "executable": str(exe), "mode": "batch from ASC"},
            "schematic": asc_path.name,
            "support_symbols": sorted(path.name for path in folder.glob("*.asy")),
            "netlist_from_schematic": net_path.name if net_path.is_file() else None,
            "netlist_connectivity_check": "LTspice batch acceptance plus IR pin labels; generated .net saved when emitted",
            "raw": raw_path.name,
            "log": log_path.name,
            "measurements": measurements,
            "comparison_to_cir_run": comparison,
            "connectivity_manifest": {reference_name: list(nodes) for reference_name, nodes in connectivity_manifest(design.ir).items()},
            "symbol_pin_mapping": connectivity_pin_map(design.ir),
            "limitations": "Generic Level 1 MOSFET, generic BJT, and self-contained behavioral op-amp models; this is simulation evidence only.",
        }
        report_path = folder / "schematic-verification.json"
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        reports.append(report_path)
    return reports


def _write_design_files(design, folder: Path) -> tuple[Path, Path, Path, dict[str, str]]:
    asc_path = folder / f"{design.design_id}.asc"
    cir_path = folder / f"{design.design_id}.cir"
    bom_path = folder / "bom.csv"
    asc_path.write_text(render_asc(design.ir, design.title), encoding="ascii", newline="\n")
    cir_path.write_text(design.ir.render(), encoding="ascii", newline="\n")
    support = required_support_files(design.ir)
    for filename, content in support.items():
        (folder / filename).write_text(content, encoding="ascii", newline="\n")
    with bom_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("Reference", "Value / model", "Quantity"))
        writer.writerows(design.bom)
    return asc_path, cir_path, bom_path, support


def _select_designs(design_ids: list[str] | None):
    if design_ids is None:
        return list(EXAMPLE_DESIGNS.values())
    unknown = set(design_ids) - set(EXAMPLE_DESIGNS)
    if unknown:
        raise ValueError("unknown design id(s): " + ", ".join(sorted(unknown)))
    if len(set(design_ids)) != len(design_ids):
        raise ValueError("duplicate design ids are not allowed")
    return [EXAMPLE_DESIGNS[key] for key in design_ids]


def _compare_measurements(reference: dict, measured: dict) -> dict:
    expected_values = _flatten(reference)
    actual_values = _flatten(measured)
    comparisons = {}
    for key in sorted(expected_values.keys() | actual_values.keys()):
        expected, actual = expected_values.get(key), actual_values.get(key)
        if isinstance(expected, (float, int)) and not isinstance(expected, bool) and isinstance(actual, (float, int)) and not isinstance(actual, bool):
            error = abs(float(actual) - float(expected)) / max(abs(float(expected)), 1e-12)
            comparisons[key] = {"cir": expected, "asc": actual, "relative_error": error, "match": error <= 0.005}
        else:
            comparisons[key] = {"cir": expected, "asc": actual, "match": expected == actual}
    return {"checks": comparisons, "all_match": all(item["match"] for item in comparisons.values()),
            "reference": "pre-existing LTspice run from generated CIR netlist"}


def _verification_fields(comparison: dict) -> dict[str, object]:
    passed = comparison.get("all_match") is True
    return {"verification_status": "passed" if passed else "failed", "design_pass": passed}


def _flatten(value: dict, prefix: str = "") -> dict[str, object]:
    flattened = {}
    for key, item in value.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(item, dict):
            flattened.update(_flatten(item, name))
        elif isinstance(item, (str, int, float, bool)) or item is None:
            flattened[name] = item
    return flattened


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate editable LTspice schematics and simulate them")
    parser.add_argument("--output-root", default="output/analog_examples_asc_v1")
    parser.add_argument("--reference-root", default="output/analog_examples_v2")
    parser.add_argument("--ltspice", help="path to LTspice.exe; auto-discovered if omitted")
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument("--design", action="append", help="design id; repeat to select multiple (default: all six)")
    parser.add_argument("--export-only", action="store_true", help="export ASC/CIR/BOM/support files without invoking LTspice")
    args = parser.parse_args()
    if args.export_only:
        reports = export_schematics(args.output_root, args.design)
        for report in reports:
            print(f"EXPORTED {report.parent.name}: {report}")
        print(f"Exported {len(reports)} editable schematics (not simulated).")
        return 0
    reports = export_and_simulate(args.output_root, args.ltspice, args.timeout, args.reference_root, args.design)
    failed = False
    for report in reports:
        details = json.loads(report.read_text(encoding="utf-8"))
        passed = details["comparison_to_cir_run"]["all_match"]
        status = "PASSED" if passed else "FAILED"
        print(f"{status} {details['design_id']}: ASC/CIR measurement comparison ({report})")
        failed |= not passed
    print(f"Completed {len(reports)}/{len(reports)} requested ASC batch simulations.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
