"""Command line entry point: python -m circuitpilot --r 1000 --c 1e-7."""

import argparse

from .rc_lowpass import build_rc_lowpass


def main() -> int:
    parser = argparse.ArgumentParser(description="Design an ideal unloaded RC low-pass filter")
    parser.add_argument("--r", type=float, required=True, help="resistance in ohms")
    parser.add_argument("--c", type=float, required=True, help="capacitance in farads")
    args = parser.parse_args()
    try:
        circuit = build_rc_lowpass(args.r, args.c)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"Analytical cutoff: {circuit.cutoff_hz:.6g} Hz (not simulation-verified)")
    print(circuit.to_spice(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
