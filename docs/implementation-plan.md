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

An earlier RL experiment is limited to RC low-pass tuning on a 3×3 discrete R/C lattice and
one fixed target. A newer offline benchmark adds explicit target-aware training and held-out
target ranges, but it still replays the same premeasured table and does not connect to behavioral
request synthesis or simulation-budgeted refinement. It does not establish generalization beyond
this fixed grid.

## Goal

Build a behavior-driven synthesis pipeline. Accept structured requirements and a bounded
sentence-style prompt grammar for current request topologies. Select a supported topology, initialize a candidate from
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
| 4. Simulation and hard verification | **Partial: nominal filter verification.** RC low-pass and buffered RC low-pass estimate passband gain from the complex one-pole asymptote and check descending cutoff; RC low-pass also supports optional gain limits. RC high-pass measures the ascending -3 dB crossing; RL low-pass measures descending cutoff; series RLC measures the output-magnitude peak; inverting op-amp measures gain magnitude at 1 kHz against requested limits. All use same-IR simulation artifacts and fail closed on missing AC data. Synthesis feasibility remains analytical. LTspice nominal runs are recorded for ideal-source RC low-pass, loaded RC low-pass, buffered RC low-pass, inverting op-amp, RC high-pass, RL low-pass, and series RLC. The 2026-10-25 high-pass, RL, and RLC checks each used 201 AC points and passed the requested 1500–1700 Hz range: measured 1591.5494309188985 Hz, 1591.5510043559714 Hz, and 1591.5494309189535 Hz respectively. Earlier timeout folders are superseded diagnostics. | Extend to requested-condition corners and other metrics; retain fail-closed launch, raw-data, measurement, and limit behavior. |
| 5. Bounded conventional refinement | **Partial: finite E-series search.** `--refine` requires `--simulate` and an explicit positive simulation budget. It ranks gain-feasible pairs first when requested, then by cutoff target/range distance and gain midpoint proximity; simulates each from the validated request; preserves per-candidate artifacts; and stops at first measured pass. | Broaden refinement strategies only with explicit candidate rules and budgets; preserve deterministic order, fail closed on simulator/measurement errors, and compare each measured result to its hard limit. |
| 6. More behaviors and topologies | **Not implemented in the behavioral request path.** Other circuits currently exist as fixed examples. | Add validated behavior/topology request contracts and design rules with tests and simulator evidence per supported family. |
| 7. Natural-language entry | **Bounded deterministic grammar implemented.** The behavioral CLI accepts sentence-style requests for RC low-pass/high-pass, RL low-pass, buffered RC low-pass, series RLC band-pass, and inverting op-amp gain, plus the legacy semicolon-labeled RC low-pass form. Complete prompts become topology-validated normalized requests; missing fields trigger clarification and ambiguous or unsupported prose is rejected. The separate measurement-requirements prompt remains a gathering aid and is not parsed. | Extend only with explicit phrase grammar and validator-backed fields; keep missing-limit clarification and unsupported phrasing fail-closed. |
| 8. Optional RL proposal policy | **Separate offline RC benchmark implemented; experimental and not integrated.** Local proposal mode remains the default and reproduces v4/v5; global mode adds nine direct candidate actions with visited-state masking. On the unchanged nine-point grid, held-out tasks, seed 20261025, 120,000 training episodes, and budget 3, repaired global v7 passed 18/45 (40.0%), versus local v5's 14/45 (31.1%). All 45 tasks are reachable by global actions, so its 27 misses are avoidable policy selections. Historical analytical ranking passed 45/45 without charging the start measurement; the start-aware comparator charges for the start and then ranks any unvisited state, passing 45/45 with 1.89 mean measurements. Pre-repair global v6 (17/45) is preserved as a diagnostic. | Diagnose policy-selection errors and compare methods with both candidate access and measurement costs made explicit. Keep any eventual suggestions behind the same simulation-backed hard verification. |
| 9. VS Code UX and release robustness | **Not implemented.** | Build after the request, simulation, verification, and refinement contracts are stable; then add robustness and release qualification evidence. |

## RL benchmark status

The offline target-aware benchmark lives in `engine/circuitpilot/rc_rl_benchmark.py` and is
invoked with `python -m circuitpilot.benchmark_rc_policy --output-dir <new-run-directory>`;
the output directory is required and must not already exist. Its module code is included in the
installable package, but there is no dedicated RL console entry point and the measured-data tree
is not bundled in the wheel. The default dataset is source-checkout-relative. From an installed
package, pass `--dataset <path-to-full-output/rl_rc_v1/benchmark.json>` and retain all referenced
simulation reports and raw traces in their relative locations; alternatively, run from the source
checkout. The benchmark uses only the nine previously measured LTspice cutoffs; no new simulator runs are launched. Before use,
the loader verifies the pinned SHA-256 for canonical benchmark content and each raw trace,
parses the raw trace, and checks that its measured cutoff matches the report and dataset. The v3
benchmark used four fixed training targets. The v4 training-side experiment uses 12,000 seeded
log-uniform targets from the measured cutoff span (482.2595–4979.5500 Hz), excluding held-out
ranges expanded by a 5% multiplicative guard. This leaves approximately 482.26–685.71,
766.50–1008.57, 1118.25–2147.62, and 2460.15–4979.55 Hz; the generated intervals and full task
manifest are saved in its artifacts. Held-out target ranges remain 720–730, 1059–1062, 1063–1065,
2255–2260, and 2338–2343 Hz; evaluation remains 45 episodes (five ranges from each of nine start
states). Seed 20261025 yielded 20/45 measured passes for v4 RL, compared with v3's 16/45. One
10× local-mode training run in v5 yielded 14/45. Conventional ranking passed 45/45 under the same budget of
three candidate measurements per task, independent of the training step cap. The diagnostic
`output/rl_rc_policy_diagnostics_v5_trace_comparison_final` shows that local RL can reach a passing grid point in
at most 33/45 task/start pairs: one budgeted measurement observes the start, leaving two adjacent
moves. Analytical ranking can jump to any of the nine states, so equal measurement budgets do not
provide equal candidate access. The detailed per-pair miss classification is saved with the
diagnostic. Every acceptance decision uses stored LTspice measurements, not analytical estimates.
No new LTspice runs were made. A same-seed, 120,000-episode local replay reproduced the saved v5
task manifest and all 45 RL traces exactly, confirming the default local path is unchanged. The
v5 local-mode run is preserved. Global v6 initially failed to stop training when the sampled start
already passed the target. After fixing that terminal-state handling, v7 used the same task
manifest generation, seed, 120,000 training episodes, held-out tasks, grid,
and budget, with nine direct R/C proposal actions and an episode visited-state mask. It passed
18/45 (40.0%), while v5 local passed 14/45 (31.1%). The final comparison is saved in
`output/rl_rc_policy_comparison_v7_final/`; the v6 run and its comparison remain preserved as
pre-repair diagnostic artifacts. Global actions make every held-out task reachable within
budget 3; v7's 27 misses were avoidable policy selections. The historical analytical baseline
passed 45/45 without charging for the initial measurement. A start-aware analytical comparator
charged for the initial measurement and then ranked any unvisited candidate; it also passed 45/45
with a mean 1.89 measurements. This comparison shows that matching measurement budgets does not
by itself make learned and analytical methods equivalent. No simulator was launched for these RL
runs. The policy remains experimental, below baseline, and not ready for integration.

This is a small fixed-grid benchmark, not evidence of generalization to other component grids,
operating conditions, topologies, or new simulator runs. The policy remains experimental and
separate from the behavioral request CLI. Its measured held-out result is below the conventional
baseline, so it is not ready for integration.

## Guardrails

- RL proposes the next bounded design action; it does not generate or run shell commands.
- Validate every action and circuit before LTspice runs.
- A missing measurement, invalid circuit, or failed simulation is never a successful design.
- The verification engine evaluates each hard constraint independently.
- Do not use user designs for training unless the user has agreed to that data use.
- Keep RL optional and retain the best verified candidate when a run stops or fails.

## Next RL work

Do not start with topology-changing RL, a graph neural network, continuous online training, or
every planned circuit family. First investigate the weak held-out result, preserve the fixed test
split, and improve the benchmark only with training-side changes. Any proposed circuit must still
pass measured hard constraints before it can be accepted.
