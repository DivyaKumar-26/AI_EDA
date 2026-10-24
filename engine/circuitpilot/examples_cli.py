"""Run the deterministic amplifier and bias-network example suite in LTspice."""

from __future__ import annotations

import argparse
import json

from .design_runner import run_example_design
from .designs import EXAMPLE_DESIGNS
from .requirements import load_spec
from .simulation import SimulationError


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate and simulate CircuitPilot example analog designs")
    parser.add_argument("--list", action="store_true", help="list available design identifiers")
    parser.add_argument("--design", action="append", choices=tuple(EXAMPLE_DESIGNS), help="design identifier (repeatable); default runs all six")
    parser.add_argument("--output-root", default="output/analog_examples", help="new parent directory for per-design artifacts")
    parser.add_argument("--ltspice", help="path to LTspice.exe; auto-discovered when omitted")
    parser.add_argument("--timeout", type=float, default=240.0, help="per-design LTspice timeout in seconds")
    parser.add_argument("--spec", help="validated JSON design requirements; requires exactly one matching --design")
    args = parser.parse_args()
    if args.list:
        for identifier, design in EXAMPLE_DESIGNS.items():
            print(f"{identifier}\t{design.title}")
        return 0

    spec = None
    if args.spec:
        try:
            spec = load_spec(args.spec)
        except ValueError as exc:
            parser.error(str(exc))
        if not args.design or len(args.design) != 1:
            parser.error("--spec requires exactly one --design")
        if args.design[0] != spec["design_id"]:
            parser.error(f"--design {args.design[0]!r} does not match specification design_id {spec['design_id']!r}")

    failures = 0
    for identifier in args.design or EXAMPLE_DESIGNS:
        design = EXAMPLE_DESIGNS[identifier]
        try:
            report = run_example_design(design, args.output_root, args.ltspice, args.timeout, spec=spec)
            if spec is not None:
                details = json.loads(report.read_text(encoding="utf-8"))
                if not details["design_pass"]:
                    failures += 1
                    print(f"INCOMPLETE {identifier}: requirement checks or requested-condition coverage failed ({report})")
                    continue
            print(f"SIMULATED {identifier}: {report}")
        except (SimulationError, OSError, ValueError) as exc:
            failures += 1
            print(f"FAILED {identifier}: {exc}")
    print(f"Completed {len(args.design or EXAMPLE_DESIGNS) - failures}/{len(args.design or EXAMPLE_DESIGNS)} requested design simulations.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
