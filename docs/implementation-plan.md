# CircuitPilot implementation plan

## Current implementation status

The request path supports structured JSON RC low-pass, RC high-pass, RL low-pass, op-amp buffered
RC low-pass, inverting op-amp amplifier, and experimental series RLC band-pass designs.
The established RC low-pass retains its optional nominal source/load and passband-gain behavior.
The added high-pass/RL handlers use a small topology dispatch, explicit component/cutoff bounds,
deterministic E-series search, and ideal source/open load; they do not yet have loading, gain
bounds, or refinement. The buffered RC handler uses the existing CP_OPAMP one-pole macro and
exports its required symbol file for simulated ASC output; it also supports only ideal source/open
load. The series RLC handler searches explicit R/C/L bounds for a requested
resonance range and measures the output peak; it has no Q/source/load constraints. Each handler
returns `CircuitIR` and derives its CIR/ASC artifacts from it.
The inverting op-amp request searches E-series input/feedback resistor pairs for an inclusive
gain-magnitude range, measures at 1 kHz, and caps gain at 100 V/V for the generic macro's finite
gain-bandwidth.
Simulation measures cutoff in the topology's crossing direction: descending for low-pass and
ascending for RC high-pass. The RC low-pass runner estimates low-frequency passband gain from the
complex one-pole AC asymptote and checks its existing optional gain bound. The
synthesis report remains analytical;
simulation verification is reported separately and fails closed on launch, raw-data, or
measurement failures. The request runner synthesizes from the request itself, then adapts the
candidate IR's AC sweep to span one decade below through one decade above its analytical cutoff;
the same adapted IR feeds both CIR and ASC output. A bounded deterministic E-series refinement
mode now preserves each attempted candidate and stops on measured pass, budget/candidate
exhaustion, or simulator/measurement failure. No hardware or production qualification is
claimed. LTspice 26.1.1.0 completed one real request at
`output/rc_behavior_request_20261024_run2`: 201 AC points measured 1591.5494309190115 Hz
against the inclusive 1500–1700 Hz range, and request verification passed with
`production_qualified: false`. `output/rc_behavior_request_20261024_run1` is a preserved 120 s
timeout diagnostic with no raw/log output and failed verification; it is not evidence.
Two real LTspice 26.1.1.0 `--refine` runs are recorded. `output/rc_behavior_refinement_20261024_run1`
used a budget of 5, stopped after one measured candidate at 1591.5494309190115 Hz, and passed
the inclusive 1500–1700 Hz range. The stronger two-candidate run used the tracked request
`docs/example-rc-lowpass-two-candidate-request.json` (the original run input is preserved at
`output/rc_behavior_refinement_20261024_two_candidate_request.json`) and artifacts at
`output/rc_behavior_refinement_20261024_two_candidate_run1`: its first candidate (1000 Ω,
100 nF) measured 1591.5494309190115 Hz and failed 1300–1500 Hz; its second (1000 Ω, 120 nF)
measured 1326.2911924324903 Hz and passed. It stopped with `first_measured_pass` after using
both budgeted simulations. Each attempt preserves CIR, ASC, IR, raw/log, and verification
evidence. Both reports keep `production_qualified: false`; this evidence applies only to these
ideal-source/open-load RC cutoff requests. A separate loaded request using
`docs/example-rc-lowpass-loaded-request.json` completed with LTspice 26.1.1.0 at
`output/rc_behavior_loaded_20261024_run2`: Rs=100 Ω, RL=10 kΩ, 201 AC points, measured cutoff
1606.0180621091863 Hz (1500–1700 Hz requested), and measured complex-asymptote passband gain
0.900900900900901 (0.89–0.92 requested); both checks passed. Analytical predictions were
1606.0180621091279 Hz and 0.9009009009009008. `production_qualified` remains false. The preserved
`output/rc_behavior_loaded_20261024_run1` used the biased sweep-start estimate (1621.894563077167
Hz, 0.8964299010900756) and is a superseded diagnostic, not evidence.

Separate from that request path, the repository contains six example design workflows, JSON
requirement evaluation, CIR/ASC simulation runners, measurements, and native-netlist comparison.
The six examples use generic `CP_NMOS`, `CP_NPN`, and `CP_OPAMP` models in generated source;
the reference device families are not integrated. README records the completed LTspice 26.1.1
six-design CIR and ASC runs, their comparison evidence, and the limitations of those results.
Those example runs do not establish that behavioral requests are simulation-verified or that
any design is hardware/production qualified.

An existing RL experiment is limited to RC low-pass tuning on a 3×3 discrete R/C lattice and
one fixed target. It trains over a premeasured table and does not connect to behavioral request
synthesis or simulation-budgeted refinement. It does not establish target-range or
cross-topology generalization.

## Goal

Build a behavior-driven synthesis pipeline. Accept structured requirements first and add a
natural-language entry point later. Select a supported topology, initialize a candidate from
explicit constraints and design rules, and create ASC and CIR artifacts from the same canonical
`CircuitIR`. Run LTspice, measure the requested behavior, and check hard limits from measured
results. Transfer-function input is optional when the topology and constraints define the
behavior. If the candidate fails, bounded conventional optimization should propose permitted
changes within explicit budgets and preserve candidate history and artifacts. RL may later
propose changes as an optional policy; deterministic simulation-backed verification remains
the acceptance authority.

## What to learn from LTSPICE-AI

The reference project demonstrates a useful workflow: create a SPICE netlist, run LTspice, read its `.raw` results, and make those results visible. CircuitPilot can use that workflow as a starting point. Its RL policy is a separate later layer; the reference project is not itself an RL design system. [Reference repository](https://github.com/kongpop10/LTSPICE-AI/tree/master)

## Build in these steps

| Step | Status | Scope and completion evidence |
|---|---|---|
| 1. Structured behavioral request | **Partial: first-order filters plus experimental series RLC band-pass and inverting op-amp amplifier.** Strict JSON validation, E-series R/C/L and resistor-pair candidate bounds, deterministic search, analytical feasibility reports, RC low-pass nominal source/load and optional passband-gain bounds, cutoff-only RC high-pass/RL low-pass/op-amp buffered RC handlers, RLC resonance-range synthesis, and inverting gain-magnitude synthesis are implemented. | Add other request behaviors/topologies and extend conditions/gain/Q constraints to newer handlers. Synthesis feasibility remains analytical. |
| 2. Topology selection and initialization | **Partial: small explicit dispatch.** Request `topology` dispatches to the established RC low-pass, RC high-pass, RL low-pass, op-amp buffered RC low-pass, inverting op-amp amplifier, or experimental series RLC handler. New handlers use ideal source/open load only. | Generalize topology choices with explicit rules and additional tests as scope grows; do not add silent defaults. |
| 3. One source of circuit artifacts | **Implemented for the RC slice.** It returns `CircuitIR` and derives both SPICE netlist and ASC from it. Separate six-example ASC/CIR flows also exist. | Keep connectivity and values tied to the same IR as topology coverage grows. |
| 4. Simulation and hard verification | **Partial: nominal filter verification.** RC low-pass and buffered RC low-pass estimate passband gain from the complex one-pole asymptote and check descending cutoff; RC low-pass also supports optional gain limits. RC high-pass measures the ascending -3 dB crossing; RL low-pass measures descending cutoff; series RLC measures the output-magnitude peak; inverting op-amp measures gain magnitude at 1 kHz against requested limits. All use same-IR simulation artifacts and fail closed on missing AC data. Synthesis feasibility remains analytical. Real RC low-pass ideal-source and loaded runs, buffered RC low-pass smoke verification, and inverting-op-amp nominal LTspice smoke verification are recorded. RC high-pass and RL smoke attempts timed out without raw/log data. The experimental RLC handler's 25-second smoke also timed out without raw/log output and failed closed. | Extend to requested-condition corners and other metrics; obtain real high-pass/RL/RLC evidence; retain fail-closed launch, raw-data, measurement, and limit behavior. |
| 5. Bounded conventional refinement | **Partial: finite E-series search.** `--refine` requires `--simulate` and an explicit positive simulation budget. It ranks gain-feasible pairs first when requested, then by cutoff target/range distance and gain midpoint proximity; simulates each from the validated request; preserves per-candidate artifacts; and stops at first measured pass. | Broaden refinement strategies only with explicit candidate rules and budgets; preserve deterministic order, fail closed on simulator/measurement errors, and compare each measured result to its hard limit. |
| 6. More behaviors and topologies | **Not implemented in the behavioral request path.** Other circuits currently exist as fixed examples. | Add validated behavior/topology request contracts and design rules with tests and simulator evidence per supported family. |
| 7. Natural-language entry | **Not implemented.** Current behavioral entry accepts JSON only; the existing requirement-gathering prompt does not parse prose. | Translate language into a reviewable structured request and ask for missing required limits rather than inventing them. |
| 8. Optional RL proposal policy | **Separate narrow RC experiment only.** Existing Q-learning replays a measured 3×3 table for one target; it is not integrated with the request pipeline or optimization budgets. | Compare against conventional optimization on held-out tasks. RL suggestions must go through the same simulation-backed hard verification. |
| 9. VS Code UX and release robustness | **Not implemented.** | Build after the request, simulation, verification, and refinement contracts are stable; then add robustness and release qualification evidence. |

## First RL task

The separate RL v1 uses only the RC low-pass topology, fixed target, and a premeasured 3×3
R/C grid. The state is the grid location; actions move one R or C index by one step. The
observation does not encode arbitrary targets or simulation budget, and action steps replay
precomputed LTspice measurements rather than launching a new simulation. It is not the
behavioral request optimizer. A future online/generalized environment must add explicit target
and budget observations before making those claims.

Keep training data and evaluation tasks separate by target ranges or parameter regions, so test results measure generalization to designs the policy did not train on. Save the seed, specification, actions, simulator measurements, reward components, and final verification result for every episode.

## Guardrails

- RL proposes the next bounded design action; it does not generate or run shell commands.
- Validate every action and circuit before LTspice runs.
- A missing measurement, invalid circuit, or failed simulation is never a successful design.
- The verification engine evaluates each hard constraint independently.
- Do not use user designs for training unless the user has agreed to that data use.
- Keep RL optional and retain the best verified candidate when a run stops or fails.

## Not in the first RL milestone

Do not start with topology-changing RL, a graph neural network, continuous online training, or every planned circuit family. First prove that the RC environment is reliable and that an RL policy beats a conventional baseline on held-out tasks.
