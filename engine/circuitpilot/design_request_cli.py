"""Compile a structured behavioral request into CircuitIR and a candidate report."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

from .rc_request import NoFeasibleCandidateError, synthesize_rc_request
from .rc_request_simulation import simulate_rc_request
from .rc_request_refinement import refine_rc_request
from .schematic import render_asc
from .simulation import SimulationError
from .topology_requests import (load_behavioral_request, simulate_topology_request,
                                synthesize_behavioral_request)
from .plain_language import parse_plain_language_request


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Synthesize a supported structured circuit request")
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--request", type=Path, help="path to a versioned JSON design request")
    input_group.add_argument(
        "--prompt",
        help="offline sentence grammar for supported behavioral topologies, plus legacy labeled RC low-pass; see docs/plain-language-request.md",
    )
    parser.add_argument("--simulate", action="store_true", help="run LTspice and verify requested behavior limits")
    parser.add_argument("--refine", action="store_true", help="try ranked E-series candidates until a measured pass or stop condition")
    parser.add_argument("--budget", type=int, help="maximum LTspice candidate simulations for --refine (required)")
    parser.add_argument("--output-dir", type=Path, help="new, unused folder for simulation artifacts (requires --simulate)")
    parser.add_argument("--ltspice", help="path to LTspice.exe")
    parser.add_argument("--timeout", type=float, default=120.0, help="LTspice timeout in seconds (default: 120)")
    args = parser.parse_args(argv)
    if args.simulate and args.output_dir is None:
        parser.error("--simulate requires --output-dir pointing to a new, unused folder")
    if not args.simulate and args.output_dir is not None:
        parser.error("--output-dir can only be used with --simulate")
    if args.refine and not args.simulate:
        parser.error("--refine requires --simulate")
    if args.refine and args.budget is None:
        parser.error("--refine requires an explicit positive --budget")
    if args.budget is not None and not args.refine:
        parser.error("--budget can only be used with --refine")
    if args.budget is not None and args.budget <= 0:
        parser.error("--budget must be a positive integer")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be finite and positive")
    prompt_metadata = None
    if args.prompt is not None:
        try:
            intake = parse_plain_language_request(args.prompt)
        except ValueError as exc:
            intake = {"status": "invalid_request", "message": str(exc)}
        if intake["status"] != "complete":
            json.dump({"intake_report": intake}, sys.stdout, indent=2, sort_keys=True)
            sys.stdout.write("\n")
            return 2
        request = intake["request"]
        prompt_metadata = {
            "input_mode": "plain_language",
            "original_prompt": args.prompt,
            "normalized_request": request,
            "intake_assumptions": intake["assumptions"],
        }
    else:
        try:
            request = load_behavioral_request(args.request)
        except ValueError as exc:
            parser.error(str(exc))
    if args.refine:
        if request["topology"] != "rc_lowpass":
            parser.error("--refine currently supports only rc_lowpass")
        try:
            refined = refine_rc_request(
                request,
                args.output_dir,
                args.budget,
                executable=args.ltspice,
                timeout_seconds=args.timeout,
            )
        except (OSError, ValueError, SimulationError) as exc:
            parser.error(str(exc))
        output = {
            "refinement_report": refined.report,
            "refinement_history": refined.history,
            "output_dir": str(args.output_dir.expanduser().resolve()),
        }
        if prompt_metadata is not None:
            output.update(prompt_metadata)
        if refined.selected_result is not None:
            result = refined.selected_result
            output.update({
                "circuit_ir": result.circuit_ir.to_dict(),
                "netlist": result.circuit_ir.render(),
                "asc": render_asc(result.circuit_ir, sheet_title="CircuitPilot RC low-pass"),
                "synthesis_report": result.report,
                "verification_report": refined.selected_verification,
            })
        json.dump(output, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0 if refined.report["design_pass"] else 1

    run = None
    alternate_report = None
    try:
        if args.simulate:
            if request["topology"] == "rc_lowpass":
                run = simulate_rc_request(
                    request,
                    args.output_dir,
                    executable=args.ltspice,
                    timeout_seconds=args.timeout,
                )
                result = run.synthesis_result
            else:
                result, alternate_report = simulate_topology_request(
                    request, args.output_dir, executable=args.ltspice, timeout_seconds=args.timeout)
        else:
            result = synthesize_behavioral_request(request)
    except NoFeasibleCandidateError as exc:
        failure = {"error": str(exc), "synthesis_report": exc.report}
        if prompt_metadata is not None:
            failure.update(prompt_metadata)
        print(json.dumps(failure, indent=2, sort_keys=True))
        return 2
    except (OSError, ValueError, SimulationError) as exc:
        parser.error(str(exc))
    output = {
        "circuit_ir": result.circuit_ir.to_dict(),
        "netlist": result.circuit_ir.render(),
        "asc": render_asc(
            result.circuit_ir,
            sheet_title="CircuitPilot RC low-pass" if request["topology"] == "rc_lowpass" else result.circuit_ir.title,
        ),
        "synthesis_report": result.report,
    }
    if prompt_metadata is not None:
        output.update(prompt_metadata)
    exit_code = 0
    if run is not None:
        output["verification_report"] = run.report
        output["output_dir"] = str(args.output_dir.expanduser().resolve())
        exit_code = 0 if run.report["design_pass"] else 1
    elif alternate_report is not None:
        output["verification_report"] = alternate_report
        output["output_dir"] = str(args.output_dir.expanduser().resolve())
        exit_code = 0 if alternate_report["design_pass"] else 1
    json.dump(output, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
