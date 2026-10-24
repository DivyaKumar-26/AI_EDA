# CircuitPilot

**AI-assisted analog circuit synthesis and verification for LTspice.**

CircuitPilot is an early Python prototype for turning explicit analog requirements into
deterministic SPICE designs, editable LTspice schematics, simulations, measurements, and
hard-constraint reports. The longer-term product idea is a VS Code extension. The current
repository is an engineering prototype, not a production design tool.

The latest behavior-driven synthesis goal is to turn a structured request, and eventually a
natural-language request, into a selected topology, an initialized candidate, reproducible
`CircuitIR`, and matching ASC/netlist artifacts. LTspice measurements and hard verification
should decide whether requested limits pass; bounded conventional optimization should refine
failed candidates, with RL available as an optional later proposal policy. Transfer functions
may be supplied when useful, but are not required when the selected topology defines the
behavior.

The implementation covers structured JSON synthesis for RC low-pass, RC high-pass, RL low-pass,
an op-amp buffered RC low-pass, an inverting op-amp amplifier, and experimental series RLC
band-pass requests. The established RC low-pass path searches E-series R/C values, optionally
includes nominal source/load resistance and a passband-gain range, and remains backward
compatible. High-pass and RL low-pass use a small topology dispatch with ideal source/open load
and cutoff-only constraints. The buffered op-amp path uses the existing generic `CP_OPAMP` macro
with an ideal source and open load. Series RLC searches explicit R/C/L bounds for a resonance range and
measures the simulated output peak; it has no Q, source/load, or refinement support. The inverting
op-amp path searches input/feedback resistor pairs for a gain-magnitude range and measures gain at
1 kHz, with a 100 V/V model-safe cap. Each
topology returns a `CircuitIR` and matching netlist/ASC. An
explicit `--simulate` mode sends the same IR through LTspice,
estimates low-frequency passband gain from complex AC samples using the one-pole asymptote,
measures cutoff relative to it, and checks the requested
cutoff and optional gain bounds. A bounded `--refine` mode searches allowed E-series R/C pairs
under an explicit simulation budget. Loaded request behavior has regression coverage and a
real LTspice run covering nominal finite source/load conditions, cutoff, and passband gain.
High-pass and RL paths do not yet support loaded conditions or bounded refinement. Continuous
optimization, operating-condition corners, and other behaviors/topologies remain unsupported in
the request path. The six-example workflows remain separate.

## Installable Python package

CircuitPilot can be installed from this repository without extra Python runtime dependencies.
It requires Python 3.10 or newer. From the repository root, install an editable checkout with:

```powershell
python -m pip install -e .
```

The `circuitpilot-design` command validates and synthesizes a JSON or supported sentence-style
request. JSON-request mode prints the `CircuitIR`, netlist, ASC text, and synthesis report;
sentence-prompt mode also includes the original prompt and `normalized_request` intake result:

```powershell
circuitpilot-design --request request.json
circuitpilot-design --prompt "Design an RC low-pass filter with cutoff from 1.5 kHz to 1.7 kHz, resistance from 1 kOhm to 10 kOhm using E12, and capacitance from 50 nF to 150 nF using E12."
```

An import-level synthesis example uses the same request validators and synthesis path as the CLI:

```python
from circuitpilot import (
    render_asc,
    synthesize_behavioral_request,
    validate_behavioral_request,
)

request = {
    "schema_version": 1,
    "topology": "rc_lowpass",
    "behavior": {"cutoff_hz": {"min": 1500, "max": 1700, "target": 1591.55}},
    "components": {
        "resistance_ohm": {"min": 1000, "max": 10000, "series": "E12"},
        "capacitance_f": {"min": 5e-8, "max": 1.5e-7, "series": "E12"},
    },
}
normalized = validate_behavioral_request(request)
result = synthesize_behavioral_request(normalized)
print(result.circuit_ir.render())
asc_text = render_asc(result.circuit_ir)
```

For the documented deterministic sentence grammar, parse to an intake report and pass its
complete request through the same validation and synthesis API:

```python
from circuitpilot import parse_plain_language_request, validate_behavioral_request

intake = parse_plain_language_request(
    "Design an RC low-pass filter with cutoff from 1.5 kHz to 1.7 kHz, resistance from 1 kOhm to 10 kOhm using E12, and capacitance from 50 nF to 150 nF using E12."
)
if intake["status"] == "complete":
    normalized = validate_behavioral_request(intake["request"])
```

The public API also exposes `CircuitIR` types and simulation functions. `simulate_rc_request`
handles RC low-pass requests and returns an `RCRequestRun`; `simulate_topology_request` handles
the other supported behavioral topologies and returns the candidate plus verification report.
Both simulation functions require a new, unused output directory and write run artifacts there.
LTspice is an external executable required only for simulation; synthesis and artifact rendering
run offline. The
`circuitpilot-examples` and `circuitpilot-schematics` commands serve the separate six-example
workflows. The experimental RL benchmark module is included in the package, but it has no
dedicated console entry point and its measured-data tree is not bundled in the wheel. The default
dataset path is source-checkout-relative, so an installed run must pass `--dataset` pointing to
the full provenance-pinned `output/rl_rc_v1/benchmark.json` tree, including every referenced
simulation report and raw trace. Otherwise, run the benchmark from the source checkout. This
remains a fixed-grid experiment, not a general-purpose RL design feature. An installed invocation
must name both the complete dataset tree and a fresh output folder:

```powershell
python -m circuitpilot.benchmark_rc_policy --dataset C:\data\rl_rc_v1\benchmark.json --output-dir C:\data\new-policy-run
```

## Project goal

Build a behavior-driven synthesis pipeline that accepts structured requests and a bounded
sentence-style prompt grammar for current request topologies. The pipeline should identify a supported topology, initialize a
candidate from explicit component bounds and design rules, and produce the `CircuitIR` used for
both ASC and CIR generation. It should run LTspice, measure requested behavior, and evaluate hard
limits from those measurements. If a candidate fails, bounded conventional optimization should
try permitted changes within explicit budgets and retain the associated history and artifacts.
An optional RL policy may propose bounded candidate changes after the deterministic workflow is
reliable; it does not decide whether a design passes.

Transfer-function input is optional when topology and behavioral constraints already describe
the request. Missing required limits, measurements, simulation results, or requested conditions
must fail closed. Simulation verification must remain distinct from hardware and production
qualification.

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

The behavioral RC request path has optional LTspice AC execution for its cutoff requirement
and optional passband-gain requirement. It accepts explicit nominal source/load conditions;
passband gain is estimated from the complex AC one-pole asymptote and cutoff is measured relative
to that estimate. Measured verification is separate from analytical synthesis feasibility and fails
closed when simulation or measurements are unavailable. Real request evidence now includes an
ideal-source/open-load cutoff run and a loaded source/load run with both cutoff and passband-gain
checks. The earlier loaded run using the biased sweep-start estimate is retained as a superseded
diagnostic only. `production_qualified` remains false.

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

### Structured RC request simulation

LTspice 26.1.1.0 completed the structured RC low-pass request at
`output/rc_behavior_request_20261024_run2`. The request's inclusive cutoff range was 1500–1700
Hz; LTspice raw data contained 201 AC points and produced a measured cutoff of
1591.5494309190115 Hz. Request verification passed, while `production_qualified` remained
`false`. This is evidence for this one RC request and cutoff check only.

`output/rc_behavior_request_20261024_run1` is a preserved failed timeout diagnostic. It timed
out after 120 seconds and produced no raw or log file; its verification failed. It is not
simulation evidence and was not used for the successful result.

The loaded request [`docs/example-rc-lowpass-loaded-request.json`](docs/example-rc-lowpass-loaded-request.json)
was verified by LTspice 26.1.1.0 at `output/rc_behavior_loaded_20261024_run2`. With Rs=100 Ω
and RL=10 kΩ, the run processed 201 AC points and measured cutoff 1606.0180621091863 Hz against
the inclusive 1500–1700 Hz range. The complex-asymptote passband estimate was
0.900900900900901 against the requested 0.89–0.92 range. Both checks passed. The analytical
candidate predicted 1606.0180621091279 Hz and gain 0.9009009009009008. The report retains
`production_qualified: false`.

The experimental RLC smoke was attempted once with a 25-second limit:

```powershell
python -m circuitpilot.design_request_cli --request docs/example-series-rlc-bandpass-request.json --simulate --output-dir output/hackathon_series_rlc_smoke_20261024 --ltspice D:\LTspice\LTspice.exe --timeout 25
```

It timed out without raw or log data; its verification report failed closed with no measured
peak. The folder is a timeout diagnostic, not simulator evidence.

`output/rc_behavior_loaded_20261024_run1` is preserved as a superseded diagnostic: it used the
biased sweep-start estimate and reported cutoff 1621.894563077167 Hz and gain estimate
0.8964299010900756. Do not use those figures as verification evidence; run2 contains the
corrected complex-asymptote measurement.

### Bounded RC refinement runs

LTspice 26.1.1.0 completed a bounded refinement of the example RC request at
`output/rc_behavior_refinement_20261024_run1`. The configured simulation budget was 5; the
search stopped after its first candidate, with 1 simulation used. The measured cutoff was
1591.5494309190115 Hz and passed the inclusive 1500–1700 Hz range. The report records
`stop_reason: first_measured_pass` and `production_qualified: false`.

A second run exercises a two-candidate search. Its tracked request is
[`docs/example-rc-lowpass-two-candidate-request.json`](docs/example-rc-lowpass-two-candidate-request.json),
and the run artifacts are in `output/rc_behavior_refinement_20261024_two_candidate_run1`. The
original run input is preserved at `output/rc_behavior_refinement_20261024_two_candidate_request.json`.
With a budget of 2, candidate 1
(R=1000 Ω, C=100 nF) measured 1591.5494309190115 Hz and failed the 1300–1500 Hz range.
Candidate 2 (R=1000 Ω, C=120 nF) measured 1326.2911924324903 Hz and passed. Refinement stopped
with `first_measured_pass` after 2 simulations. Both candidate directories preserve their CIR,
ASC, IR, raw/log, and verification report. The top-level report keeps
`production_qualified: false`. These runs demonstrate bounded search and measured RC cutoff
verification only.

Earlier folders `output/ltspice_26_1_1_asc_20261024`,
`output/ltspice_26_1_1_asc_validated_20261024`, and
`output/ltspice_26_1_1_asc_final_20261024` are partial diagnostic runs. Use the complete
folder above as the six-design ASC evidence set.

### First-order topology breadth smoke checks

The previously verified RC low-pass request run at `output/rc_behavior_request_20261024_run2`
remains the successful LTspice low-pass evidence. A new smoke invocation in
`output/hackathon_rc_lowpass_smoke_20261024_retry` timed out after 120 seconds and produced no
raw/log; it is a preserved diagnostic, not a replacement for the successful run.

The new-topology smoke commands were:

```powershell
python -m circuitpilot.design_request_cli --request docs/example-rc-highpass-request.json --simulate --output-dir output/hackathon_rc_highpass_smoke_20261024 --ltspice D:\LTspice\LTspice.exe --timeout 30
python -m circuitpilot.design_request_cli --request docs/example-rl-lowpass-request.json --simulate --output-dir output/hackathon_rl_lowpass_smoke_20261024_retry --ltspice D:\LTspice\LTspice.exe --timeout 20
python -m circuitpilot.design_request_cli --request docs/example-opamp-rc-lowpass-request.json --simulate --output-dir output/hackathon_opamp_rc_lowpass_smoke_20261024 --ltspice D:\LTspice\LTspice.exe --timeout 25
```

The RC high-pass and RL low-pass smoke attempts timed out without raw or log data. The initial
25-second buffered-op-amp smoke also timed out and remains a preserved diagnostic. A later
LTspice run with a 120-second timeout completed successfully at
[`output/hackathon_opamp_rc_lowpass_retry_120s_20261024`](output/hackathon_opamp_rc_lowpass_retry_120s_20261024/request-verification.json):
201 AC points measured the cutoff at 1591.2274175955174 Hz against an analytical prediction of
1591.5494309189537 Hz (−0.322013323 Hz, −0.020233%). The requested 1500–1700 Hz range passed;
the [LTspice log](output/hackathon_opamp_rc_lowpass_retry_120s_20261024/design.log) is saved
beside the report. This is nominal smoke verification only; `production_qualified` remains
`false`. Regression tests cover the RC high-pass ascending crossing, RL low-pass generated
circuit, and buffered RC simulation measurement seam. The initial high-pass and RL timeout
attempts are superseded by the successful 2026-10-25 reports below. Request reports keep
`production_qualified: false`.

The inverting-op-amp request initially timed out at 120 seconds with an AC-only source and a
shorter sweep. A fresh retry matches the fixed example's `DC 0 AC 1` source and 10 Hz–100 MHz
sweep. LTspice completed 701 AC points and measured 9.998896080913909 V/V at 1 kHz against the
requested 9.5–10.5 V/V range. The [verification report](output/hackathon_inverting_opamp_smoke_retry_120s_20261024/request-verification.json)
and [LTspice log](output/hackathon_inverting_opamp_smoke_retry_120s_20261024/design.log) are in
`output/hackathon_inverting_opamp_smoke_retry_120s_20261024`; the failed run remains preserved
in `output/hackathon_inverting_opamp_smoke_120s_20261024`. This is nominal smoke evidence only;
`production_qualified` remains `false`.

The later RC high-pass, RL low-pass, and experimental series RLC smoke checks completed
successfully with 201 AC points each. High-pass measured 1591.5494309188985 Hz versus
1591.5494309189537 Hz expected; RL low-pass measured 1591.5510043559714 Hz versus
1591.5494309189535 Hz expected; RLC measured resonance 1591.5494309189535 Hz, matching its
prediction. All three passed the requested inclusive 1500–1700 Hz range. Reports: [RC high-pass](output/hackathon_rc_highpass_verify_20261025/request-verification.json), [RL low-pass](output/hackathon_rl_lowpass_verify_20261025/request-verification.json), [series RLC](output/hackathon_series_rlc_verify_20261025/request-verification.json). Earlier timeout folders remain superseded diagnostics. These are nominal smoke checks; `production_qualified` remains `false`.

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

### Target-aware RC policy benchmark (offline)

`engine/circuitpilot/rc_rl_benchmark.py` adds an experimental, offline target-aware benchmark
using only those same nine existing LTspice-measured RC grid values. It checks a pinned SHA-256
for the canonical benchmark content and for every raw trace, parses each ASCII trace, and requires
its cutoff to match both the benchmark and its simulation report. For the v4 training-side
experiment, 12,000 targets are sampled log-uniformly from the measured cutoff span
(482.2595–4979.5500 Hz), excluding the five held-out ranges after expanding each by a 5%
multiplicative guard band. This leaves approximately 482.26–685.71, 766.50–1008.57,
1118.25–2147.62, and 2460.15–4979.55 Hz. The exact intervals and full seeded task manifest
are saved with the run. Evaluation remains the same five held-out ranges × nine starting states (45 tasks).
Seed `20261025`, policy/configuration, per-episode actions/rewards/measurements/stop reasons, and
summary are saved in `output/rl_rc_target_benchmark_v4/`; the 120,000-episode run is in
`output/rl_rc_target_benchmark_v5/`. Earlier v1–v3 folders are preserved as prior experiment
evidence.

Reproduce v4 with the same seed and configuration in a new output folder (the CLI refuses to
overwrite an existing run):

```powershell
$env:PYTHONPATH='engine'; python -m circuitpilot.benchmark_rc_policy --output-dir output/rl_rc_target_benchmark_replay1 --seed 20261025 --training-episodes 12000 --candidate-budget 3 --training-max-steps 8
```

The one-time 10× v5 local-proposal run used:

```powershell
$env:PYTHONPATH='engine'; python -m circuitpilot.benchmark_rc_policy --output-dir output/rl_rc_target_benchmark_v5_replay1 --seed 20261025 --training-episodes 120000 --candidate-budget 3 --training-max-steps 8
```

The predeclared global-proposal comparison used the same seed, 120,000 training episodes,
held-out tasks, dataset, and three-measurement budget. Global actions can propose any of the nine
measured R/C states directly, while masking states already measured in the episode. The legacy
local proposal mode remains the default and reproduces v4/v5 behavior; a same-seed, 120,000-episode
local replay reproduced v5's task manifest and all 45 RL episode traces exactly (14/45 passes).
The initial global run v6 is preserved as a pre-repair diagnostic. After fixing terminal handling
for training starts that already meet target, the final v7 run and comparison are saved in
`output/rl_rc_target_benchmark_v7/` and `output/rl_rc_policy_comparison_v7_final/`:

```powershell
$env:PYTHONPATH='engine'; python -m circuitpilot.benchmark_rc_policy --output-dir output/rl_rc_target_benchmark_v7_replay1 --seed 20261025 --training-episodes 120000 --candidate-budget 3 --training-max-steps 8 --proposal-mode global
```

With a budget of three candidate measurements per task, v4 passed 20/45 held-out tasks (44.4%),
up from v3's 16/45 (35.6%). The single predeclared 10× training run in v5 passed 14/45 (31.1%),
so additional training reduced held-out performance. Conventional analytical ranking passed
45/45 (100%) using the same stored LTspice measurements as acceptance evidence. The diagnostic
in `output/rl_rc_policy_diagnostics_v5_trace_comparison_final/` finds only 33/45 task/start pairs can reach a
passing candidate with RL's action graph and this budget: the first measurement is the start
state, leaving two adjacent R/C moves. Thus 12 misses are structurally unavoidable at budget 3;
the remaining 33 task/start pairs are structurally reachable. v5 passed 14 of those 33 and missed
19: 11 by selecting the wrong candidate/direction and 8 by repeating or stalling. v4 passed 20
and missed 13 of the same 33 (8 candidate-selection and 5 repeated/stalled misses). Equal
measurement budgets do not imply equal candidate access: analytical
ranking can jump directly to any of the nine grid states and passed all 45 tasks. This benchmark
launches no LTspice runs and does not establish generalization beyond the fixed grid or evaluation
ranges. In the repaired v7 global run, RL passed 18/45 (40.0%), versus local v5's 14/45 (31.1%).
All 45 held-out tasks are theoretically reachable by global proposals within budget 3, so its 27
misses are avoidable policy selections. The preserved pre-repair v6 run passed 17/45 (37.8%).
The historical analytical baseline passed 45/45 without
charging for the start measurement; a new start-aware comparator charges for that measurement and
then ranks any unvisited grid point, also passing 45/45 with 1.89 mean measurements. This exposes
that equal measurement budgets do not imply equal learning quality even after matching candidate
access. Per-task traces and miss classifications are in the v7 comparison output. The benchmark
remains experimental, is not connected to `design_request_cli`, and `production_qualified` remains
`false`.

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

The measurement-requirements prompt below is for gathering information and is not parsed by
that workflow. Separately, the behavioral CLI supports an offline `--prompt` grammar for
sentence-style requests across the current RC/RL filter, buffered RC, series RLC, and inverting
op-amp topologies, plus the legacy labeled RC low-pass form. It returns a normalized request or
clarification and rejects unsupported or ambiguous wording. This is bounded phrase parsing,
not general prose understanding. See [the plain-language intake guide](docs/plain-language-request.md).
Unknown requirements remain unknown; do not invent values to fill them.
The measurement-requirements runner supports the six listed designs and their declared
fields/metrics. The separate structured behavioral path supports the RC/RL filter, buffered RC,
series RLC, and inverting op-amp request topologies; its initial RC low-pass contract is
documented below.

For the initial behavioral synthesis path, see
[`docs/rc-lowpass-request.md`](docs/rc-lowpass-request.md) and
[`docs/example-rc-lowpass-request.json`](docs/example-rc-lowpass-request.json). The request
requires a cutoff range and explicit R/C bounds and E-series; a target frequency is optional.
The structured schema is [`schemas/rc-lowpass-request.schema.json`](schemas/rc-lowpass-request.schema.json).

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
python -m circuitpilot.design_request_cli --request docs/example-rc-lowpass-request.json
python -m circuitpilot.design_request_cli --request docs/example-rc-lowpass-request.json --simulate --output-dir output/<new-request-run> --ltspice <path-to-LTspice.exe> --timeout 120
python -m circuitpilot.design_request_cli --request docs/example-rc-lowpass-request.json --simulate --refine --budget 5 --output-dir output/<new-refinement-run> --ltspice <path-to-LTspice.exe> --timeout 120
python -m circuitpilot.design_request_cli --request docs/example-rc-lowpass-loaded-request.json --simulate --output-dir output/<new-loaded-request-run> --ltspice <path-to-LTspice.exe> --timeout 120
python -m circuitpilot.design_request_cli --request docs/example-rc-highpass-request.json --simulate --output-dir output/<new-highpass-run> --ltspice <path-to-LTspice.exe> --timeout 120
python -m circuitpilot.design_request_cli --request docs/example-rl-lowpass-request.json --simulate --output-dir output/<new-rl-run> --ltspice <path-to-LTspice.exe> --timeout 120
python -m circuitpilot.design_request_cli --request docs/example-series-rlc-bandpass-request.json --simulate --output-dir output/<new-series-rlc-run> --ltspice <path-to-LTspice.exe> --timeout 120
python -m circuitpilot.design_request_cli --request docs/example-opamp-rc-lowpass-request.json --simulate --output-dir output/<new-opamp-rc-run> --ltspice <path-to-LTspice.exe> --timeout 120
python -m circuitpilot.design_request_cli --request docs/example-inverting-opamp-request.json --simulate --output-dir output/<new-inverting-opamp-run> --ltspice <path-to-LTspice.exe> --timeout 120
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

The structured request command also has an explicit `--simulate` mode; it requires a new,
unused `--output-dir`, estimates passband gain from complex LTspice AC data, measures cutoff
relative to that gain, and checks the requested cutoff and optional gain range.
Add `--refine --budget <positive-integer>` to try deterministically ranked component pairs and
stop on the first measured pass, exhausted budget/candidate set, or simulator/measurement
failure. Each attempt is preserved in a separate candidate folder with top-level history and
summary reports. See [docs/rc-lowpass-request.md](docs/rc-lowpass-request.md) for ranking,
stop rules, artifact set, and fail-closed behavior.
The same CLI dispatches `rc_highpass`, `rl_lowpass`, `opamp_rc_lowpass`, and experimental
`series_rlc_bandpass`, plus `inverting_opamp_amplifier`; see their example JSON files. The op-amp buffered RC low-pass uses the
existing generic one-pole `CP_OPAMP` macro and exports `CP_OPAMP.asy` beside simulated ASC files.
It supports only ideal-source/open-load cutoff checks. RC
high-pass cutoff uses an ascending -3 dB crossing, while RL low-pass uses a descending crossing.
The inverting op-amp demo checks gain magnitude at 1 kHz with an inclusive request range and a
100 V/V limit imposed by the macro's finite gain-bandwidth. These additions currently support only ideal-source/open-load nominal checks, without gain/Q
bounds, operating-condition corners, or refinement. Series RLC is an experimental handler and
checks resonance-frequency bounds only.

Available tests were run with `$env:PYTHONPATH='engine'; python -m unittest discover -s tests/unit -v`;
all 105 tests passed. `python -m pytest -q` could not run because `pytest` is not installed in
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
- `engine/circuitpilot/rc_request.py`: behavioral RC request validation, E-series candidate
  search, fail-closed feasibility, and synthesis report.
- `engine/circuitpilot/design_request_cli.py`: JSON request synthesis and optional simulation CLI.
- `engine/circuitpilot/rc_request_simulation.py`: same-IR ASC/CIR generation, LTspice execution,
  complex-AC asymptote gain and relative-cutoff measurements, hard verification, and request-run artifacts.
- `engine/circuitpilot/rc_request_refinement.py`: bounded deterministic E-series candidate
  refinement, per-attempt evidence, and ordered history/summary reports.
- `engine/circuitpilot/topology_requests.py`: small topology dispatch and RC high-pass/RL low-pass
  synthesis plus experimental series RLC synthesis and nominal measurement handlers.
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

Extend the structured RC request path from nominal source/load conditions and cutoff/passband
gain to operating-condition corners and additional measurements, and broaden the current bounded
E-series refinement beyond first measured pass.
Extend the request and initialization pipeline to
additional behavior/topology families while keeping unsupported behavior explicit. Expand the
offline prompt grammar beyond the current supported topologies and strict sentence patterns while
preserving clarification for missing limits and explicit rejection of unsupported phrasing.
Evaluate RL as an optional proposal policy against the conventional
baseline on held-out tasks. A VS Code experience, broader robustness work, and release
qualification follow once these stages have evidence. The existing six-design simulation and
ASC/CIR comparison results remain evidence for those example workflows only; they do not
qualify the behavior-driven request path or establish production/hardware qualification.

The long-term design principle remains: AI proposes; deterministic tools generate and check;
LTspice simulates; explicit evidence decides whether declared requirements pass.
