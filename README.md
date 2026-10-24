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

The repository's synced baseline is `26fe3c9`, which contains the six-design schematic grid
exporter and generic demonstration models. The current working tree adds strict JSON
requirement validation/evaluation, nominal supply/source-resistance/load insertion for CIR
spec runs, and a native `.net` parser/comparator used by the ASC simulation workflow. These
changes have unit coverage. LTspice 26.1.1 has completed all six CIR simulations and all six
ASC native-netlist simulations; every ASC comparison matches its CIR report. The exact
artifacts and limits of this evidence are recorded below. Final independent review of the
implementation and simulator evidence is complete. The reviewer checked MOS, BJT, and op-amp
native-netlist normalization against actual LTspice 26.1.1 netlists and audited all six CIR/
ASC reports and comparisons; the review found no actionable issues.

The implementation remains incomplete. It does not yet execute requested temperature,
supply, resistor, or capacitor corners; apply transient input amplitude/frequency; measure
THD or output swing; provide vendor device models; or apply a user specification to the ASC
export. Spec reports show numerical limit results separately and keep the overall design
failed while requested operating-condition coverage is incomplete. The review does not
establish production or hardware qualification; do not treat this source state as a qualified
release.

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

The device/model column names the intended reference device families. The current generated
simulations still use the generic `CP_NMOS`, `CP_NPN`, and `CP_OPAMP` models in
`engine/circuitpilot/designs.py`; the named reference devices are not yet used by these
designs.

The documented reference conditions use a 12 V single supply for transistor designs, ±15 V
for op-amp designs, 50 Ω source resistance, 10 kΩ amplifier load, 10 mV peak at 1 kHz, 1%
resistor tolerance, 10% capacitor tolerance, and temperature points within 0–70 °C. These
values are reference assumptions, not user-confirmed product requirements.

The proposed common-emitter change is to add 100 Ω of unbypassed emitter feedback to reduce
distortion while keeping total DC emitter resistance near 1 kΩ. This change is not applied
in `designs.py`: the current circuit has a 1 kΩ emitter resistor fully bypassed by `CE`.
The proposed circuit's BOM, corner behavior, gain, and distortion have not been verified.

## Results and evidence

### Current LTspice 26.1.1 six-design run

LTspice 26.1.1 completed the six CIR simulations in
`output/ltspice_26_1_1_cir_20261024`, with one `verification-report.json` per design. The
reports completed and passed their available topology checks. This run does not cover the
unimplemented user-specification corners or transient metrics described above.

The matching ASC workflow completed all six native netlist compilations and simulations in
`output/ltspice_26_1_1_asc_complete_20261024`. Every native netlist passed the IR pin, value,
and model checks. All measurements match the corresponding CIR report: the per-design
comparison has 16 checks for each bias network and op-amp, and 21 checks for each amplifier
design, for 106 checks total in the saved reports. Every numeric relative error is 0.0. The
reports set `production_qualified` to `false`; these results establish simulation agreement
for the current generic models and implemented analyses only.

Earlier folders `output/ltspice_26_1_1_asc_20261024`,
`output/ltspice_26_1_1_asc_validated_20261024`, and
`output/ltspice_26_1_1_asc_final_20261024` are partial diagnostic runs. Use the complete
folder above as the six-design ASC evidence set.

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

### Historical hardening diagnostic notes

The following v1/v2 run descriptions are historical notes. Their
`output/production_reference_v1` and `output/production_reference_v2` folders are not present
in this synced clone.

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

## Known limitations and improvements required

- Add and run the requested temperature, supply, resistor, and capacitor corner matrix,
  including exact scenario coverage in each report.
- Evaluate the proposed common-emitter feedback change against the 5% THD reference ceiling
  without loosening the target to fit the result; the change is not yet applied to the design.
- Apply validated user specifications to ASC generation and simulation, with matching limits
  and condition coverage in the ASC reports.
- Add transient input amplitude/frequency, THD, and output-swing measurements, then verify
  those metrics in both CIR and ASC workflows.
- Repeat independent review after changes to the implementation or simulator evidence.
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

Available tests were run with `$env:PYTHONPATH='engine'; python -m unittest discover -s tests/unit -v`;
all 54 tests passed. `python -m pytest -q` could not run because `pytest` is not installed in
the environment. The six-design LTspice evidence is complete at the paths above and received
independent review with no actionable findings. The historical v1/v2 diagnostic notes do not
establish a final design pass.

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
- `engine/circuitpilot/schematic_validation.py`: native LTspice `.net` IR pin/value checker.
- `engine/circuitpilot/schematics_cli.py`: schematic export and ASC-generated netlist
  simulation workflow.
- `engine/circuitpilot/train_rc_policy.py`: fixed-grid RC measured-table Q-learning
  experiment.
- `schemas/`: topology, IR, and design-requirements schemas.
- `tests/unit/`: unit and integration regressions for the current source.
- `output/`: preserved run artifacts. Never overwrite or remove old evidence to make a new
  run appear clean.

## Handoff for the next agent

1. Start by reading `AGENTS.md`, this README, `git status`, and the current diff. The synced
   baseline commit is `26fe3c9`; substantial work remains uncommitted.
2. Treat `output/analog_examples_v2` and `output/rl_rc_v1` as historical completed evidence.
   Preserve incomplete `production_reference_v1` and `production_reference_v2` folders as
   diagnostics only if encountered in another working tree; do not call them final evidence.
3. Inspect the current source and review requirement validation, nominal source/load wiring,
   native `.net` checking, reference device models, common-emitter feedback, and the missing
   transient/corner workflows. Add or repair tests where needed.
4. Run the authoritative project test suite from this root. The current six-design CIR
   evidence is in `output/ltspice_26_1_1_cir_20261024`; run a fresh CIR suite if source changes.
5. The current ASC evidence is in `output/ltspice_26_1_1_asc_complete_20261024`. If source
   changes, run ASC against matching fresh CIR reports and require native `.net` validation,
   simulation completion, and report comparisons for all six designs.
6. After any new simulator run, record exact artifact paths, simulator version, results, and
   test command here. Preserve `production_qualified: false`.
7. Independent review of the current implementation and six-design reports is complete with
   no actionable findings. Repeat review after changes and retain `production_qualified: false`.

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
