"""Bounded deterministic E-series candidate refinement for structured RC requests."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import subprocess
from typing import Callable, Mapping

from .rc_request import (
    RCSynthesisResult,
    rc_request_candidate_count,
    validate_rc_request,
)
from .rc_request_simulation import RCRequestRun, simulate_rc_request
from .simulation import SimulationError


@dataclass(frozen=True)
class RCRefinementRun:
    report: dict
    history: dict
    report_path: Path
    history_path: Path
    selected_result: RCSynthesisResult | None
    selected_verification: dict | None


def refine_rc_request(
    request: Mapping,
    output_dir: str | Path,
    simulation_budget: int,
    executable: str | None = None,
    timeout_seconds: float = 120.0,
    runner: Callable = subprocess.run,
) -> RCRefinementRun:
    """Simulate bounded, deterministically ranked R/C pairs and stop at the first measured pass.

    Out-of-range measurements continue to the next candidate. Any simulator or measurement
    failure stops the run immediately. A fresh output directory is mandatory.
    """
    if isinstance(simulation_budget, bool) or not isinstance(simulation_budget, int) or simulation_budget <= 0:
        raise ValueError("simulation_budget must be a positive integer")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be finite and positive")
    directory = Path(output_dir).expanduser().resolve()
    if directory.exists():
        raise SimulationError(f"Refusing to overwrite existing refinement artifacts in {directory}")
    spec = validate_rc_request(dict(request))
    candidate_count = rc_request_candidate_count(spec)
    directory.mkdir(parents=True)

    history_path = directory / "refinement-history.json"
    report_path = directory / "refinement-report.json"
    history = {
        "schema_version": 1,
        "status": "running",
        "simulation_budget": simulation_budget,
        "budget_used": 0,
        "candidate_space_size": candidate_count,
        "stop_reason": None,
        "attempts": [],
    }
    _write_json(history_path, history)

    selected_result = None
    selected_verification = None
    stop_reason = None
    maximum_attempts = min(simulation_budget, candidate_count)
    for candidate_index in range(maximum_attempts):
        attempt_number = len(history["attempts"]) + 1
        candidate_folder_name = f"candidate-{attempt_number:03d}"
        candidate_folder = directory / candidate_folder_name
        try:
            run: RCRequestRun = simulate_rc_request(
                spec,
                candidate_folder,
                executable=executable,
                timeout_seconds=timeout_seconds,
                runner=runner,
                candidate_index=candidate_index,
            )
        except (OSError, ValueError, SimulationError) as exc:
            history["attempts"].append({
                "attempt": attempt_number,
                "candidate_index": candidate_index,
                "folder": candidate_folder_name,
                "simulation_status": "failed",
                "verification_status": "failed",
                "design_pass": False,
                "failure_kind": "candidate_or_artifact_error",
                "error": str(exc),
            })
            stop_reason = "candidate_or_artifact_failure"
            _write_json(history_path, history)
            break

        candidate_report = run.synthesis_result.report
        verification = run.report
        attempt = {
            "attempt": attempt_number,
            "candidate_index": candidate_index,
            "folder": candidate_folder_name,
            "candidate": candidate_report["candidate"],
            "analytical_candidate_feasible": candidate_report["status"] == "feasible",
            "simulation_status": verification["simulation_status"],
            "verification_status": verification["verification_status"],
            "measured_cutoff_hz": verification["measurements"]["cutoff_hz"],
            "measured_passband_gain_estimate_from_complex_ac": verification["measurements"][
                "passband_gain_estimate_from_complex_ac"],
            "design_pass": verification["design_pass"],
            "failure_kind": verification.get("failure_kind"),
            "error": verification.get("error"),
            "report": f"{candidate_folder_name}/{run.report_path.name}",
        }
        history["attempts"].append(attempt)
        _write_json(history_path, history)

        if verification["design_pass"]:
            selected_result = run.synthesis_result
            selected_verification = verification
            stop_reason = "first_measured_pass"
            break
        if verification.get("failure_kind") in {"simulation_failed", "measurement_failed"}:
            stop_reason = "simulation_or_measurement_failure"
            break

    if stop_reason is None:
        if candidate_count == 0:
            stop_reason = "no_standard_value_candidates"
        elif simulation_budget < candidate_count:
            stop_reason = "simulation_budget_exhausted"
        else:
            stop_reason = "candidate_space_exhausted"

    design_pass = selected_result is not None
    status = "passed" if design_pass else "failed"
    history.update({
        "status": status,
        "budget_used": len(history["attempts"]),
        "stop_reason": stop_reason,
    })
    summary = {
        "schema_version": 1,
        "topology": "rc_lowpass",
        "status": status,
        "verification_status": "passed" if design_pass else "failed",
        "design_pass": design_pass,
        "production_qualified": False,
        "candidate_evidence": "analytical",
        "verification_evidence": "LTspice_measurement" if design_pass else "incomplete_or_failed",
        "simulation_budget": simulation_budget,
        "budget_used": len(history["attempts"]),
        "candidate_space_size": candidate_count,
        "stop_reason": stop_reason,
        "history": history_path.name,
        "selected_candidate": (
            {
                **selected_result.report["candidate"],
                "folder": history["attempts"][-1]["folder"],
                "verification_report": history["attempts"][-1]["report"],
            }
            if design_pass else None
        ),
        "error": (
            history["attempts"][-1].get("error")
            if history["attempts"] and not design_pass else None
        ),
    }
    _write_json(history_path, history)
    _write_json(report_path, summary)
    return RCRefinementRun(summary, history, report_path, history_path, selected_result, selected_verification)


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
