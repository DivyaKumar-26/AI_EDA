"""LTspice execution and hard cutoff verification for structured RC requests."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import math
from pathlib import Path
import subprocess
from typing import Callable, Mapping

from .rc_request import (
    RCSynthesisResult,
    synthesize_rc_request,
    synthesize_rc_request_candidate,
    validate_rc_request,
)
from .rc_lowpass import format_spice_number
from .schematic import render_asc
from .simulation import (
    SimulationError,
    estimate_one_pole_passband_gain,
    measure_cutoff_relative_to_passband,
    parse_ascii_ac_raw,
    run_spice,
)


@dataclass(frozen=True)
class RCRequestRun:
    synthesis_result: RCSynthesisResult
    report: dict
    report_path: Path


def simulate_rc_request(
    request: Mapping,
    output_dir: str | Path,
    executable: str | None = None,
    timeout_seconds: float = 120.0,
    runner: Callable = subprocess.run,
    candidate_index: int | None = None,
) -> RCRequestRun:
    """Simulate the synthesized IR in a new folder and verify its measured cutoff.

    A new folder is required. Analytical feasibility remains in the synthesis report;
    only the LTspice raw-file measurement determines the request verification result.
    """
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be finite and positive")
    directory = Path(output_dir).expanduser().resolve()
    if directory.exists():
        raise SimulationError(f"Refusing to overwrite existing run artifacts in {directory}")
    normalized_request = validate_rc_request(dict(request))
    synthesized = (
        synthesize_rc_request(normalized_request)
        if candidate_index is None
        else synthesize_rc_request_candidate(normalized_request, candidate_index)
    )
    result = _with_candidate_sweep(synthesized)
    directory.mkdir(parents=True)

    ir_path = directory / "circuit-ir.json"
    request_path = directory / "request.json"
    asc_path = directory / "design.asc"
    bom_path = directory / "bom.json"
    report_path = directory / "request-verification.json"
    spec = normalized_request
    cutoff_bounds = spec["behavior"]["cutoff_hz"]
    gain_bounds = spec["behavior"].get("passband_gain")
    constraints = [{
        "name": "cutoff_hz",
        "min": cutoff_bounds["min"],
        "max": cutoff_bounds["max"],
        "measured": None,
        "pass": False,
        "evidence": "missing_simulation_measurement",
    }]
    if gain_bounds is not None:
        constraints.append({
            "name": "passband_gain",
            "min": gain_bounds["min"],
            "max": gain_bounds["max"],
            "measured": None,
            "pass": False,
            "evidence": "missing_simulation_measurement",
        })
    artifact_names = {
        "netlist": "design.cir",
        "schematic": asc_path.name,
        "circuit_ir": ir_path.name,
        "request": request_path.name,
        "bom": bom_path.name,
        "raw": "design.raw",
        "log": "design.log",
        "verification_report": report_path.name,
    }

    ir_path.write_text(json.dumps(result.circuit_ir.to_dict(), indent=2) + "\n", encoding="utf-8")
    request_path.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    asc_path.write_text(render_asc(result.circuit_ir, sheet_title="CircuitPilot RC low-pass"), encoding="utf-8")
    bom_path.write_text(json.dumps(_bom(result), indent=2) + "\n", encoding="utf-8")

    report = {
        "schema_version": 1,
        "topology": "rc_lowpass",
        "analytical_candidate_feasibility": {
            "status": result.report["status"],
            "pass": result.report["status"] == "feasible",
            "evidence": "analytical",
            "candidate": result.report["candidate"],
        },
        "applied_operating_conditions": result.report["applied_operating_conditions"],
        "simulation_status": "failed",
        "verification_status": "failed",
        "design_pass": False,
        "production_qualified": False,
        "analysis": "LTspice AC; passband gain is estimated from the one-pole complex transfer asymptote, and cutoff is the descending -3.0103 dB crossing relative to that estimate",
        "ac_sweep_hz": _sweep_report(result),
        "requested_cutoff_hz": {"min": cutoff_bounds["min"], "max": cutoff_bounds["max"]},
        "requested_passband_gain": gain_bounds,
        "measurements": {
            "cutoff_hz": None,
            "passband_gain_estimate_from_complex_ac": None,
            "passband_gain_method": "median real(1/H) across complex AC points, converted to a one-pole DC gain estimate",
            "evidence": None,
        },
        "hard_constraints": constraints,
        "artifacts": artifact_names,
        "error": None,
    }

    try:
        batch = run_spice(
            result.circuit_ir,
            "design",
            directory,
            executable=executable,
            timeout_seconds=timeout_seconds,
            runner=runner,
        )
    except SimulationError as exc:
        netlist_path = directory / artifact_names["netlist"]
        if not netlist_path.exists():
            netlist_path.write_text(result.circuit_ir.render(), encoding="ascii", newline="\n")
        report["error"] = str(exc)
        report["failure_kind"] = "simulation_failed"
        report["simulator"] = {"name": "LTspice", "mode": "batch AC analysis"}
        report["artifacts_present"] = {name: (directory / filename).is_file()
                                       for name, filename in artifact_names.items() if name != "verification_report"}
        _write_report(report_path, report)
        return RCRequestRun(result, report, report_path)

    report["simulation_status"] = "completed"
    report["simulator"] = {"name": "LTspice", "executable": batch.simulator_executable, "mode": "batch AC analysis"}
    try:
        raw_text = batch.raw_path.read_text(encoding="ascii", errors="replace")
        source_node = next(element.nodes[0] for element in result.circuit_ir.elements
                           if element.reference.casefold() == "v1")
        points = parse_ascii_ac_raw(raw_text, input_name=f"V({source_node})", output_name="V(out)")
        passband_gain = estimate_one_pole_passband_gain(points)
        measured = measure_cutoff_relative_to_passband(points, passband_gain)
    except (OSError, SimulationError) as exc:
        report["error"] = str(exc)
        report["failure_kind"] = "measurement_failed"
        report["artifacts_present"] = {name: (directory / filename).is_file()
                                       for name, filename in artifact_names.items() if name != "verification_report"}
        _write_report(report_path, report)
        return RCRequestRun(result, report, report_path)

    cutoff_passes = cutoff_bounds["min"] <= measured <= cutoff_bounds["max"]
    report["measurements"] = {
        "cutoff_hz": measured,
        "passband_gain_estimate_from_complex_ac": passband_gain,
        "passband_gain_method": "median real(1/H) across complex AC points, converted to a one-pole DC gain estimate",
        "evidence": "LTspice ASCII AC raw data",
        "ac_points": len(points),
    }
    report["hard_constraints"][0].update({
        "measured": measured,
        "pass": cutoff_passes,
        "evidence": "simulation_measurement",
    })
    constraint_passes = [cutoff_passes]
    if gain_bounds is not None:
        gain_passes = gain_bounds["min"] <= passband_gain <= gain_bounds["max"]
        report["hard_constraints"][1].update({
            "measured": passband_gain,
            "pass": gain_passes,
            "evidence": "simulation_measurement",
        })
        constraint_passes.append(gain_passes)
    passes = all(constraint_passes)
    report["verification_status"] = "passed" if passes else "failed"
    report["design_pass"] = passes
    if not passes:
        report["failure_kind"] = "constraint_failed"
        failed_names = [constraint["name"] for constraint in report["hard_constraints"] if not constraint["pass"]]
        report["error"] = "measured hard constraint(s) failed: " + ", ".join(failed_names)
    report["artifacts_present"] = {name: (directory / filename).is_file()
                                   for name, filename in artifact_names.items() if name != "verification_report"}
    _write_report(report_path, report)
    return RCRequestRun(result, report, report_path)


def _with_candidate_sweep(result: RCSynthesisResult) -> RCSynthesisResult:
    """Return the same candidate IR with an AC sweep bracketing its expected cutoff."""
    candidate_cutoff = result.report["candidate"]["cutoff_hz"]
    low = candidate_cutoff / 10.0
    high = candidate_cutoff * 10.0
    if not math.isfinite(low) or low <= 0 or not math.isfinite(high):
        raise ValueError("candidate cutoff cannot be bracketed by a finite AC sweep")
    ac_indices = [index for index, directive in enumerate(result.circuit_ir.directives)
                  if directive.casefold().startswith(".ac ")]
    if len(ac_indices) != 1:
        raise ValueError("RC request CircuitIR must contain exactly one AC sweep directive")
    directives = list(result.circuit_ir.directives)
    directives[ac_indices[0]] = f".ac dec 100 {format_spice_number(low)} {format_spice_number(high)}"
    circuit = replace(result.circuit_ir, directives=tuple(directives))
    return replace(result, circuit_ir=circuit)


def _sweep_report(result: RCSynthesisResult) -> dict[str, float]:
    cutoff = result.report["candidate"]["cutoff_hz"]
    return {"min": cutoff / 10.0, "max": cutoff * 10.0, "expected_cutoff_hz": cutoff}


def _bom(result: RCSynthesisResult) -> dict:
    items = []
    for element in result.circuit_ir.elements:
        if element.reference[0].upper() not in {"R", "C"}:
            continue
        items.append({
            "reference": element.reference,
            "value": element.body,
            "nodes": list(element.nodes),
        })
    return {"items": items}


def _write_report(path: Path, report: dict) -> None:
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
