"""Command line entry point: python -m circuitpilot --r 1000 --c 1e-7."""

import argparse
from pathlib import Path

from .rc_lowpass import build_rc_lowpass
from .simulation import SimulationError, run_ltspice


def main() -> int:
    parser = argparse.ArgumentParser(description="Design an ideal unloaded RC low-pass filter")
    parser.add_argument("--r", type=float, required=True, help="resistance in ohms")
    parser.add_argument("--c", type=float, required=True, help="capacitance in farads")
    parser.add_argument("--simulate", action="store_true", help="run the generated netlist in local LTspice")
    parser.add_argument("--output-dir", type=Path, default=Path("output/ltspice_run"), help="simulation artifact directory")
    parser.add_argument("--ltspice", help="path to LTspice.exe (auto-discovered when omitted)")
    parser.add_argument("--timeout", type=float, default=120.0, help="LTspice timeout in seconds")
    args = parser.parse_args()
    try:
        circuit = build_rc_lowpass(args.r, args.c)
    except ValueError as exc:
        parser.error(str(exc))
    if args.simulate:
        try:
            result = run_ltspice(circuit, args.output_dir, args.ltspice, args.timeout)
        except (SimulationError, ValueError) as exc:
            parser.error(str(exc))
        print(f"LTspice simulated cutoff: {result.simulated_cutoff_hz:.6g} Hz")
        print(f"Analytical cutoff: {result.analytical_cutoff_hz:.6g} Hz")
        print(f"Difference: {result.cutoff_error_percent:+.4g}%")
        print(f"Report: {result.report_path}")
    else:
        print(f"Analytical cutoff: {circuit.cutoff_hz:.6g} Hz (not simulation-verified)")
        print(circuit.to_spice(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
