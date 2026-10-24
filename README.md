# CircuitPilot

**AI-assisted analog circuit synthesis and verification for LTspice.**

CircuitPilot is an early Python prototype for turning explicit analog requirements into
deterministic SPICE designs, editable LTspice schematics, simulations, measurements, and
hard-constraint reports. The longer-term product idea is a VS Code extension. The current
repository is an engineering prototype, not a production design tool.

## Project goal

The immediate goal is to make a small set of supported circuits reproducible and verifiable
before expanding topology coverage or adding optimization. LTspice produces the electrical
evidence. The deterministic verification code checks measurements against explicit limits.
An AI or learned policy may suggest candidates, but it does not establish electrical
correctness.

The intended workflow is:

1. Gather a complete, explicit electrical specification.
2. Validate supported values, units, metrics, and limits.
3. Build a canonical `CircuitIR` and derive deterministic netlists and schematics from it.
4. Run LTspice and preserve the generated input, raw output, log, BOM, and report.
5. Measure the required behavior and fail closed when evidence or a required metric is
   missing.
6. Report simulation verification separately from hardware or production qualification.

## Current project state

The committed baseline is `9a09a23`. It contains the six-design schematic grid exporter and
earlier generic demonstration models. There are additional uncommitted changes in the
working tree. Those changes are still in progress, have not completed an independent review,
and do not have a successful final six-design simulator run.

The in-progress hardening work includes:

- Named LTspice library entries: 2N7002, 2N3904, and LT1001, with library paths and hashes
  recorded when available.
- A shared IR-based netlist/schematic workflow, including three-pin native VDMOS and
  five-pin LT1001 mapping.
- Strict JSON operating conditions and hard measurement constraints for the six examples.
- AC, operating-point, DC-sweep, and transient measurements, plus sampled supply,
  temperature, resistor, and capacitor corners.
- ASC native `.net` compilation and connectivity validation support.
- A user-facing design requirement template with a copy/paste prompt and a machine-readable
  example.

These source changes should be treated as an implementation candidate. Some bugs were found
and patched during development; the full review and final evidence run remain outstanding.
Do not treat an in-progress source state as a validated release.

## Supported examples

The current reference set contains:

| Design | Circuit | Reference device/model |
|---|---|---|
| `common-source-mosfet-amplifier` | Common-source amplifier | LTspice 2N7002 |
| `common-emitter-bjt-amplifier` | Common-emitter amplifier | LTspice 2N3904 |
| `inverting-opamp-amplifier` | Inverting amplifier | LT1001 |
| `non-inverting-opamp-amplifier` | Non-inverting amplifier | LT1001 |
| `mosfet-bias-network` | MOSFET divider bias | LTspice 2N7002 |
| `bjt-bias-network` | BJT divider bias | LTspice 2N3904 |

The documented reference conditions use a 12 V single supply for transistor designs, ±15 V
for op-amp designs, 50 Ω source resistance, 10 kΩ amplifier load, 10 mV peak at 1 kHz, 1%
resistor tolerance, 10% capacitor tolerance, and temperature points within 0–70 °C. These
values are reference assumptions, not user-confirmed product requirements.

The common-emitter design was changed during hardening to add 100 Ω of unbypassed emitter
feedback, intended to reduce distortion while keeping total DC emitter resistance near 1 kΩ.
That updated circuit has not yet received a final LTspice run. Its BOM, corner behavior,
gain, and distortion still need verification.

## Results and evidence

### Historical completed artifacts

`output/analog_examples_v2` contains completed CIR-based LTspice runs from the earlier
generic-model implementation. They are historical evidence, not evidence for the current
device models or schematics.

- Common-source gain at 1 kHz: 23.0232364 V/V; the sweep had no high-cutoff crossing.
- Common-emitter gain at 1 kHz: 228.406844 V/V; measured high cutoff: 12.9289038 MHz.
- Earlier behavioral op-amp gains: 9.998896 and 10.998786 V/V; high cutoff: 1.44635475 MHz.
  These values do not describe the present LT1001 designs.

`output/rl_rc_v1/benchmark.json` records a small RC policy experiment. LTspice measured a
fixed 3×3 grid for a 1591.5494 Hz target. The learned greedy policy reached the 1% target
tolerance from 9/9 start states with 1.33 mean actions. A seeded random walk succeeded in
296/450 runs (65.8%); the best grid point had 0.0039179% error. This is a finite fixed-grid
comparison, not held-out or cross-topology RL evidence. Training replays a premeasured
table.

### Current hardening diagnostics

`output/production_reference_v1` stopped during its first common-source run because the
transient path passed an `ExampleDesign` where it required a `CircuitIR`. That incomplete
folder must not be presented as a completed matrix.

`output/production_reference_v2` completed only 2/6 designs. Both bias networks passed. The
amplifier nominal analyses ran, but corner simulation stopped because a rewritten `VIN`
source contained malformed repeated `SIN(...)` parameters. That source rewrite was
subsequently made idempotent, but the final-source matrix has not been run.

The v2 nominal waveform diagnostics measured common-source gain about 25.94 V/V, output peak
about 0.263 V, and estimated THD 1.36%. The common-emitter circuit measured estimated THD
about 8.55%, above the declared 5% reference ceiling. The inverting and non-inverting op-amp
gains were about 10.00235 and 11.00261 V/V, with very low measured THD. The common-emitter
feedback change described above is a proposed correction; it is not yet verified.

A separate 2N7002 probe measured a high cutoff near 4.62 MHz. Probes validate only the
specific probe decks and conditions, not the complete reference designs.

LTspice 26.0.1 successfully compiled a minimal ASC probe to `.net` with `-netlist` and an
explicit symbol path. Native compilation and simulation of all six current ASC files are
still pending. A minimal probe is not proof of the six schematic exports.

## Known limitations and improvements required

- Complete a fresh run from the final source for all six CIR designs, including nominal
  AC/OP or DC analyses, transient checks for amplifiers, and every required corner.
- Review the common-emitter feedback adjustment against the 5% THD reference ceiling without
  loosening the target to fit the result.
- Run each ASC through LTspice native netlisting, compare its device pins and values with
  the IR, then simulate the emitted `.net` file and compare measured AC/OP or DC outputs
  with the matching CIR report.
- Check every report’s scenario aggregation, source-to-output measurement, missing-cutoff
  behavior, temperature schedule, and recorded component values.
- Complete an independent review of all current uncommitted changes and repair verified
  findings.
- Verify exact manufacturer model provenance and selected supplier/package pinouts.
  Installed library files are opaque and hashed, not independently audited.
- Check component voltage, current, power, temperature, capacitor polarity, and derating
  against selected physical parts.
- Expand tolerance and process analysis beyond the current sampled corners. The existing
  matrix is not every independent combination or a process-corner analysis.
- Treat gain, distortion, stability, startup, clipping, and thermal results as simulation
  measurements for these models only. No circuit has production or hardware qualification.

## Requirements template

Use [docs/design-requirements-template.md](docs/design-requirements-template.md) to collect
requirements. It has a copy/paste prompt, a JSON example, supported metric names, and the
current machine-readable contract at
[schemas/design-requirements.schema.json](schemas/design-requirements.schema.json).

The natural-language prompt is for gathering information. The engine does not parse prose.
Translate the answers into the validated JSON specification before asking the current engine
to evaluate limits. Unknown requirements remain unknown; do not invent values to fill them.
The current engine only supports the six listed designs and its declared fields and metrics.

## Reproducible commands

Run from the directory containing this README. In this checkout that project root is
`D:\circuit_design\circuit_Design`; the outer `D:\circuit_design` directory is only a
container.

```powershell
Set-Location <project-root>
$env:PYTHONPATH = "engine"
python -m circuitpilot --r 1000 --c 1e-7
python -m circuitpilot.examples_cli --list
python -m circuitpilot.examples_cli --design inverting-opamp-amplifier --output-root output/<new-run-folder> --timeout 240
python -m circuitpilot.examples_cli --design inverting-opamp-amplifier --spec docs/example-inverting-opamp-spec.json --output-root output/<new-spec-run-folder> --timeout 240
python -m circuitpilot.schematics_cli --export-only --output-root output/<new-asc-export-folder>
python -m circuitpilot.schematics_cli --output-root output/<new-asc-run-folder> --reference-root output/<matching-cir-run-folder> --timeout 240
python -m pytest -q
```

The placeholders must be replaced with new, unused output folder names because runs refuse
to overwrite artifacts. LTspice for Windows is required for simulation; pass `--ltspice
<path-to-LTspice.exe>` if discovery fails. The ASC simulation command expects a completed
CIR report tree at `--reference-root`.

The RC command generates a deterministic netlist and analytical cutoff estimate without
requiring LTspice. Add `--simulate --output-dir output/<new-rc-run-folder>` to run its
LTspice AC sweep and measure the transfer cutoff. The RC sweep is fixed from 1 Hz to 1 MHz;
a cutoff outside that band cannot be measured.

No final simulator or test command was run after the latest hardening edits. Do not quote a
test count or claim a final design pass from the interrupted v1/v2 runs.

## Source map

- `engine/circuitpilot/ir.py`: canonical elements, topology registry-facing IR, and
  deterministic SPICE rendering.
- `engine/circuitpilot/designs.py`: six reference designs, device library references, and
  BOM entries.
- `engine/circuitpilot/design_runner.py`: LTspice invocation, raw-data measurements,
  reference scenarios, constraints, and reports.
- `engine/circuitpilot/requirements.py`: supported requirement metrics, units, JSON
  validation, and fail-closed evaluation.
- `engine/circuitpilot/schematic.py`: ASC rendering, symbol mapping, connectivity manifests,
  and support symbols.
- `engine/circuitpilot/schematic_validation.py`: native LTspice `.net` compilation and IR
  pin/value checking.
- `engine/circuitpilot/schematics_cli.py`: schematic export and ASC-generated netlist
  simulation workflow.
- `engine/circuitpilot/train_rc_policy.py`: fixed-grid RC measured-table Q-learning
  experiment.
- `schemas/`: topology, IR, and design-requirements schemas.
- `tests/unit/`: unit and integration regressions for the current source.
- `output/`: preserved run artifacts. Never overwrite or remove old evidence to make a new
  run appear clean.

## Handoff for the next agent

1. Start by reading `AGENTS.md`, this README, `git status`, and the current diff. The
   baseline commit is `9a09a23`; substantial work remains uncommitted.
2. Treat `output/analog_examples_v2` and `output/rl_rc_v1` as historical completed evidence.
   Preserve incomplete `production_reference_v1` and `production_reference_v2` folders as
   diagnostics; do not call them final evidence.
3. Inspect the current source and confirm the waveform-call fix, idempotent `SIN(...)`
   rewrite, source-resistance wiring, common-emitter feedback change, requirement
   validation, and ASC native-netlist integration. Add or repair tests where needed.
4. Run the authoritative project test suite from this root. Then run the complete CIR suite
   into a fresh output folder and review all nine recorded scenarios per design.
5. Run the ASC workflow against the corresponding current CIR reports. Require successful
   native `.net` compilation, IR connectivity/value match, successful `.net` simulation, and
   meaningful report comparison for every design.
6. Update this README with the exact final artifact path, simulator version, per-design
   results, scenario pass counts, ASC comparison status, and exact test command only after
   those checks succeed.
7. Have an independent reviewer examine the resulting source and reports, repair verified
   issues, rerun affected evidence, and retain `production_qualified: false`.

The user has requested GPT-6 Luna for implementation and the independent reviewer. Follow
that preference when available. Push or commit only when the user explicitly requests it.

## Future plan

After the six examples and exports pass review, keep the supported topology and requirement
boundary explicit. Improve device provenance, BOM and package validation, realistic
input/load coverage, stability and transient checks, then broaden the reference requirements
and independent corner coverage. Only after those verification gates are reliable should the
project add bounded optimization for additional circuits, held-out RL tasks,
natural-language requirement parsing, or a VS Code user interface.

The long-term design principle remains: AI proposes; deterministic tools generate and check;
LTspice simulates; explicit evidence decides whether declared requirements pass.
