# CircuitPilot

**AI-assisted analog circuit synthesis and verification for VS Code and LTspice.**

CircuitPilot is a planned VS Code extension that turns electrical design requirements into candidate analog circuits, generates editable LTspice schematics and SPICE netlists, runs local simulations, optimizes component values, and checks simulated performance against explicit constraints.

> **Project status:** Early-stage project. Implemented items below are limited to the Python netlist engine, six LTspice examples, and a fixed-grid RC policy experiment. CircuitPilot must not claim a design passes until required measurements have been obtained from an actual simulation and all stated hard constraints have been evaluated.

See the [plain-language implementation plan](docs/implementation-plan.md) for the staged path from a verified RC workflow to an optional RL policy.

### Implemented foundation: RC low-pass and simulation examples

The Python engine supports a validated first-order RC low-pass workflow, six deterministic LTspice example designs, a reusable `CircuitIR`/topology registry, and a tabular RC tuning experiment. The default RC CLI reports the ideal analytical cutoff; its opt-in simulation mode runs LTspice. The example library covers MOSFET/BJT common-source/common-emitter amplifiers, inverting/non-inverting op-amp amplifiers, and MOSFET/BJT divider bias networks. It does not evaluate arbitrary user-supplied product constraints.

The Python engine package is under `engine/circuitpilot`. The project root is the directory containing this `README.md`. If the project is nested inside a container directory, use the inner project root; the outer container does not contain the Python package. From the project root, with Python 3.10 or newer:

```powershell
Set-Location <project-root>
$env:PYTHONPATH = "engine"
python -m circuitpilot --r 1000 --c 1e-7
```

The example uses 1 kΩ and 100 nF, giving an ideal cutoff near 1.59 kHz. The generated `.ac` directive always sweeps from 1 Hz through 1 MHz. The analytical-only command does not run or inspect that sweep.

To run the netlist in an installed LTspice for Windows and measure its simulated -3.0103 dB cutoff, use:

```powershell
python -m circuitpilot --r 1000 --c 1e-7 --simulate --output-dir output/ltspice_run
```

LTspice is discovered from `PATH` or its standard per-user install location; pass `--ltspice <path-to-LTspice.exe>` if automatic discovery does not find it. Simulation writes `rc_lowpass.cir`, LTspice `.raw`/`.log` outputs, and `verification-report.json` into the selected directory. Existing artifacts with those names are not overwritten. The cutoff is measured from `|V(out)/V(in)|` against the unity passband in log-frequency and dB. Since the sweep is fixed at 1 Hz to 1 MHz, a cutoff below the lower limit is reported as outside the sweep, and a cutoff above the upper limit has no measured crossing. The report distinguishes a completed simulation from design verification: without a hard target constraint, its verification status is `not_evaluated` and `design_pass` is false. The `.cir` netlist is the simulation input; the six-design ASC exporter is described below.

To run focused unit tests, install `pytest` and run `python -m pytest` from the project root.

### Reusable topology API and RC policy experiment

`CircuitIR` is the deterministic netlist representation; `TopologyRegistry` validates topology parameters against declared schemas before calling registered factories. The registry currently contains the RC low-pass topology plus the six example topologies. The JSON schemas in `schemas/` describe the current request shape and IR structure; this is an extensible engine seam, not general-purpose analog synthesis.

The optional RC policy experiment builds a measured 3×3 R/C candidate grid by running LTspice, then trains dependency-free tabular Q-learning on those measurements:

```powershell
python -m circuitpilot.train_rc_policy --output-root output/rl_rc_v1
```

Pass `--ltspice <path-to-LTspice.exe>` if discovery fails. This takes multiple simulator launches and can take several minutes. It preserves existing candidate artifacts and writes a checkpoint, measurement-backed benchmark, and per-candidate simulation outputs. The v1 policy is limited to one fixed target and one finite RC grid; it does not generalize across targets or topologies. Its benchmark compares a learned greedy policy with a random walk on that same grid and an exhaustive-grid oracle, not a broad held-out benchmark. LTspice measurements ground the environment rewards; the Q-learning episodes replay the finite measured table rather than launching a simulator on every action.

The current recorded run in `output/rl_rc_v1/benchmark.json` used nine LTspice measurements for a 1.59155 kHz target. The learned greedy policy reached the 1% target tolerance from 9/9 grid states (mean 1.33 actions); the seeded random-walk baseline reached it on 296/450 runs (65.8%, mean 4.90 actions). The best grid point was within 0.00392% of target. These figures describe this finite benchmark only.

### LTspice example amplifier and bias suite

The example suite writes a SPICE netlist, BOM, raw simulation output, LTspice log, and measurement report for each selected design. All six run by default, each in a new subfolder; existing per-design folders are never overwritten.

```powershell
python -m circuitpilot.examples_cli --list
python -m circuitpilot.examples_cli --output-root output/analog_examples
```

Run a single example by repeating `--design` as needed, for example:

```powershell
python -m circuitpilot.examples_cli --design inverting-opamp-amplifier --output-root output/analog_examples
```

Amplifier reports include operating-point voltages, AC gain and phase at 1 kHz, peak gain, and interpolated −3 dB bandwidth. MOSFET and BJT amplifier reports also include bias checks. The bias network designs perform 9–15 V DC sweeps and check the transistor operating region at each sweep point. A report's `design_pass` reflects only the stated topology checks; it does not qualify the design against a product specification. The op-amp examples use a self-contained one-pole behavioral model and omit important real-device limits such as slew rate, input offset, supply rails, and output current limiting. MOSFET and BJT examples use generic self-contained models. These are simulation examples for exploration, not production-ready designs. Both `.cir` and `.asc` files are generated from the same IR; ASC simulation status is tracked separately.

### Editable ASC export

Export the six examples as editable LTspice schematics, with the canonical `.cir`, BOM, connectivity manifest, and custom symbol support beside each file:

```powershell
python -m circuitpilot.schematics_cli --export-only --output-root output/analog_examples_asc_v2
```

The exporter maps symbol pins using LTspice `SpiceOrder` and assigns each IR net to a labeled flag connected by a short wire. Its connectivity manifest records both IR terminal order and symbol pin names. The op-amp sheets include a local `CP_OPAMP.asy`; model definitions and analysis directives are placed on the sheet.

ASC batch simulation was not verified in the current environment: batch attempts here did not produce `.net` or `.raw` outputs. The exported ASC files are structurally checked against the IR and reports mark `simulation_status` as `not_run`. Successful CIR-based LTspice simulation results remain separately available in `output/analog_examples_v2`; those results do not establish that the ASC files themselves simulate.

## Vision

The intended workflow is:

1. Describe the circuit and its electrical requirements.
2. Convert the requirements into a validated, machine-readable specification.
3. Select a suitable topology and propose initial component values.
4. Generate a canonical circuit representation.
5. Export an editable LTspice schematic (`.asc`) and a SPICE netlist.
6. Run LTspice locally on Windows.
7. Extract measurements and optimize component values.
8. Re-simulate the selected candidate and evaluate every hard constraint.
9. Export the design, simulation evidence, BOM and verification report.

**Design principle:** AI proposes; deterministic tools generate and optimize; LTspice simulates; the verification engine decides whether the requirements pass.

## Goals

- Natural-language and structured circuit specifications.
- AI-assisted topology selection and initial design proposals.
- Deterministic generation of netlists and editable schematics from one canonical circuit representation.
- Local Windows LTspice simulation.
- Bounded component optimization with explicit tolerances.
- Constraint-based verification and actionable failure reports.
- Reproducible design runs, candidate history and exportable artifacts.
- Optional integration with compatible AI tools and MCP servers.

## Initial scope

The first supported domain is analog circuits, developed incrementally.

### Initial topology targets

- RC low-pass and high-pass filters.
- Common-source MOSFET amplifiers.
- Common-emitter BJT amplifiers.
- Inverting and non-inverting op-amp amplifiers.
- MOSFET and BJT bias networks.

Later topology targets may include active filters, differential amplifiers, current mirrors, multistage amplifiers and compensation networks.

Each topology must explicitly define its supported requirements, component models, parameter bounds, analyses, measurement methods and verification rules. Support should not be claimed until the topology has passed its automated tests.

## Proposed architecture

```text
VS Code Extension (TypeScript)
  ├── Design specification UI
  ├── Commands, progress and cancellation
  ├── Results and artifact views
  └── Local engine process management
             │
             ▼
Python Design Engine
  ├── Specification parser and validator
  ├── AI topology planner
  ├── Canonical Circuit IR
  ├── Topology registry
  ├── SPICE netlist exporter
  ├── LTspice schematic exporter
  ├── Optimization engine
  ├── Simulation adapter
  ├── Measurement extraction
  ├── Constraint verification
  └── Artifact and report manager
             │
             ▼
Local Windows LTspice
  ├── Operating-point analysis
  ├── AC analysis
  ├── Transient analysis
  └── DC sweeps and other supported analyses
```

### Core architectural rule

The circuit IR is the single source of truth. The schematic and netlist must be generated from the same candidate representation and checked for connectivity consistency. The AI should return validated structured data, not arbitrary shell commands or unchecked final files.

### Suggested repository layout

```text
circuitpilot/
├── extension/
│   └── src/
│       ├── extension.ts
│       ├── commands/
│       ├── panels/
│       ├── services/
│       └── webview/
├── engine/
│   └── circuitpilot/
│       ├── api/
│       ├── specification/
│       ├── ai/
│       ├── circuit/
│       ├── spice/
│       ├── optimization/
│       ├── verification/
│       └── artifacts/
├── schemas/
│   ├── design-spec.schema.json
│   ├── circuit-ir.schema.json
│   └── verification-report.schema.json
├── templates/
│   ├── rc_filter/
│   ├── common_source/
│   ├── common_emitter/
│   ├── active_filter/
│   └── bias_network/
├── examples/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── end_to_end/
│   ├── golden/
│   └── fault_injection/
├── docs/
├── package.json
├── README.md
└── LICENSE
```

This is a proposed structure; it may be adjusted as implementation begins.

## Requirements and constraints

Specifications should support:

- Target values with absolute or relative tolerances.
- Minimum, maximum and range constraints.
- Multiple simultaneous hard constraints.
- Soft objectives such as lower power, fewer components or lower cost.
- Explicit units, supply conditions, load conditions and component bounds.
- Preferred component series such as E12 or E24.
- Simulation budgets, wall-clock limits and optional robustness runs.

Examples of requirements include:

- Cutoff frequency near 1 kHz within a specified tolerance.
- Voltage gain near −10 V/V.
- Bandwidth greater than a minimum.
- Input resistance above a threshold.
- Power dissipation below a maximum.
- A transistor operating point within a valid region.

Tolerance handling must be metric-aware. For example, gain in dB and gain in V/V are different metrics. Relative error is not suitable for a zero target; an absolute tolerance should be used instead.

## Optimization approach

The optimizer should:

1. Validate the specification and parameter bounds.
2. Obtain one or more candidate topologies and analytical initial estimates.
3. Simulate candidates through LTspice.
4. Extract the required measurements.
5. Evaluate hard constraints independently.
6. Rank feasible candidates before considering soft objectives.
7. Select the next candidate using a suitable bounded, derivative-free or discrete search method.
8. Quantize eligible values to preferred component series.
9. Re-simulate the final quantized candidate.
10. Preserve the best candidate and explain any unresolved violations.

Potential algorithms include differential evolution for broad, nonconvex searches and bounded local or discrete search for refinement. Algorithm choice should be based on benchmark results and simulation cost.

A failed simulation, missing measurement or unresolved hard constraint must never be reported as a passing design.

## Verification strategy

Verification has several layers:

### Structural verification

- Unique component references.
- Valid pins and connected nets.
- Correct ground and supply connectivity.
- Valid parameter bounds and model dependencies.
- Supported directives and components.
- Schematic/netlist consistency.

### Electrical verification

- Run the analyses required by the specification.
- Measure actual circuit behavior using LTspice results.
- Check every hard constraint against its own tolerance and units.
- Check topology-specific conditions such as transistor bias regions, voltage headroom and power.

### Robustness verification

Where supported, evaluate component tolerances, Monte Carlo variation, temperature, supply and load changes, and device-model corners. Reports must distinguish **nominally verified** from **robustness verified** and state which variations were tested.

## Artifacts

A completed design run is intended to produce a project directory similar to:

```text
output/
└── <design-id>/
    ├── specification.json
    ├── candidates/
    ├── final/
    │   ├── design.asc
    │   ├── design.cir
    │   └── bom.csv
    ├── simulations/
    │   ├── simulation.log
    │   └── measurements.json
    └── reports/
        └── verification-report.json
```

Actual artifact names and formats may evolve. Paths in reports should be relative to the design workspace where practical.

## Development roadmap

1. **Foundation:** repository, extension scaffold, Python engine, LTspice discovery and batch execution.
2. **Specification:** schemas, units, hard and soft constraints, validation and clarification handling.
3. **Circuit IR:** components, nets, models, parameters and topology registry.
4. **Netlist generation:** deterministic SPICE rendering and validation.
5. **Simulation:** LTspice adapter, analyses, measurement extraction and failure handling.
6. **Optimization:** bounded search, candidate ranking, caching, budgets and component quantization.
7. **Schematic generation:** editable `.asc` output, symbol mapping and connectivity checks.
8. **AI planning:** provider abstraction, structured topology proposals and feedback from simulation.
9. **Topology library:** filters, transistor amplifiers, op-amp circuits and bias networks.
10. **Verification:** regression tests, analytical checks, robustness analysis and reports.
11. **VS Code UX:** design panel, progress, results, plots, commands and artifact navigation.
12. **Interoperability:** evaluate optional MCP and external AI-tool integrations.
13. **Release:** documentation, security review, clean-machine testing and VSIX packaging.

## Testing plan

- Unit tests for schemas, units, constraints, circuit IR and exporters.
- Golden tests for known-good circuits and generated files.
- Integration tests against an installed LTspice instance.
- End-to-end tests from specification to verified artifacts.
- Regression tests for previously passing designs.
- Fault-injection tests for missing models, invalid AI output, convergence failures, timeouts, missing measurements and cancellation.
- Clean Windows installation tests for extension packaging and runtime dependencies.

An initial smoke test should use a first-order RC low-pass with `R = 1 kΩ` and `C = 100 nF`, whose ideal cutoff frequency is approximately 1.59 kHz. The test should compare the measured cutoff with the analytical value and confirm the expected frequency shift when component values change.

## Security and reliability

- Restrict file operations to the intended project workspace.
- Validate structured AI outputs before using them.
- Do not execute AI-generated shell commands.
- Use explicit executable paths and argument arrays for subprocesses.
- Apply timeouts, cancellation and isolated simulation directories.
- Treat external model files and SPICE directives as untrusted inputs.
- Preserve previous candidates rather than overwriting them during optimization.
- Make external AI-provider data sharing explicit and configurable.
- Record simulator versions, model dependencies and run settings.
- Clearly distinguish simulation results from physical hardware guarantees.

## Technology choices

| Area | Proposed technology |
|---|---|
| VS Code extension | TypeScript, VS Code Extension API |
| Local engine | Python |
| Schemas and validation | JSON Schema and Pydantic |
| Numerical optimization | SciPy and NumPy |
| Circuit simulation | Local Windows LTspice |
| Testing | pytest and an extension test framework |
| Packaging | VS Code Extension Manager (`vsce`) |
| Optional interoperability | MCP adapters where useful |

Dependencies and specific API choices should be confirmed during implementation. The initial version should not require MCP to run simulations; direct local LTspice integration keeps the core workflow independently testable.

## Useful references

- [LTspice official site](https://www.analog.com/en/resources/design-tools-and-calculators/ltspice-simulator.html)
- [VS Code Extension API](https://code.visualstudio.com/api)
- [VS Code extension testing](https://code.visualstudio.com/api/working-with-extensions/testing-extension)
- [SciPy optimization](https://docs.scipy.org/doc/scipy/reference/optimize.html)
- [Pydantic documentation](https://docs.pydantic.dev/)
- [LTspice MCP integration to evaluate](https://github.com/Cognitohazard/ltspice-mcp)

## Project status and contribution

CircuitPilot is intended to evolve incrementally. The first end-to-end milestone is a supported RC filter workflow that generates a netlist and editable schematic, simulates locally, adjusts component values to meet a target, and produces a verification report.

New topology support should include its implementation, measurement definitions, schematic and netlist generation, tests, and documentation. A topology should not be marked supported merely because an AI model can describe it.

Contributions should prioritize reproducible results, explicit engineering assumptions, robust failure handling and testable behavior.

---

**CircuitPilot: AI-assisted circuit design, grounded in simulation and verification.**
