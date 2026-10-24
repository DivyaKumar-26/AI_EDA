# Structured RC low-pass requests

The first behavior-driven synthesis slice accepts a JSON request for a first-order RC low-pass
filter. The request supplies an inclusive cutoff range, an optional preferred cutoff target,
explicit resistor and capacitor bounds, and an E-series for each component. It may also specify
nominal source/load resistance and an optional passband-gain range. Required fields are not
defaulted. The transfer function need not be supplied: the selected topology defines the
behavior, with output across the capacitor. For an ideal source and open load,
`H(s) = 1 / (1 + sRC)`. With source/load conditions, `H(s) = G0 / (1 + s C Rth)`, where
`Rth = (Rs + R) || RL` for a finite load or `Rs + R` for an open load, and `G0` is the
low-frequency divider gain (`RL / (Rs + R + RL)` for a finite load, otherwise 1).

The accepted fields and physical search limits are specified in
[`schemas/rc-lowpass-request.schema.json`](../schemas/rc-lowpass-request.schema.json). The
engine supports E3, E6, E12, and E24 nominal values. All component values use SI units: ohms
and farads. A target ranks candidates; it must fall inside the hard cutoff range but does not
replace that range. If `operating_conditions` is omitted, synthesis uses an ideal source and an
open load, preserving the original canonical RC circuit. If present, it must contain exactly
`source_resistance_ohm` (finite and nonnegative) and `load_resistance_ohm` (null for open load,
otherwise finite and positive). Optional `behavior.passband_gain` limits must satisfy
`0 < min <= max <= 1`.

For series resistor `R`, source resistance `Rs`, load `RL`, and capacitor `C`, the ideal-source,
open-load special case has `Rth = R`, `fc = 1 / (2πRC)`, and passband gain 1. In general,
`Rth = (Rs + R) || RL` (or `Rs + R` for an open load), `fc = 1 / (2π C Rth)`, and passband
gain is `RL / (Rs + R + RL)` (or 1 for an open load).
Analytical feasibility and refinement ranking include a requested gain range when present;
simulation measurements remain the acceptance authority.

Run the example request:

```powershell
$env:PYTHONPATH = "engine"
python -m circuitpilot.design_request_cli --request docs/example-rc-lowpass-request.json
```

The command emits JSON containing the selected `CircuitIR`, its rendered netlist, an ASC
schematic rendered from that same `CircuitIR`, and a deterministic synthesis report. The report
includes the selected values, analytical loaded cutoff and passband-gain estimates,
hard-constraint results, number of candidate
pairs searched, and ranking rule. With no target, the ranking reference is the geometric center
of the requested cutoff range. Ties are resolved by lower resistance and then lower capacitance.
This is a transparent candidate-selection rule, not an electrical optimum.

If no standard-value pair satisfies all requested hard constraints and component bounds, including
the cutoff and optional passband-gain ranges, the API raises
`NoFeasibleCandidateError` with an infeasibility report; the CLI emits that report and exits
with status 2. In both feasible and infeasible reports, `constraint_evidence` and each
hard-constraint entry's `evidence` are `analytical`; a constraint's `pass` field records only
the standard-value search result. In no-simulation mode, `verification_status` is
`not_simulated`, and `production_qualified` is `false`. The candidate cutoff is analytical
(`1 / (2π C Rth)`, reducing to `1 / (2πRC)` for an ideal source/open load); these constraint
flags are not simulation-backed acceptance results. The request
path's constraint flags do not establish simulation-backed acceptance, hardware, or production
qualification.

To run this request through the existing LTspice batch runner, choose a new output directory:

```powershell
python -m circuitpilot.design_request_cli --request docs/example-rc-lowpass-request.json `
  --simulate --output-dir output/<new-request-run> --ltspice <path-to-LTspice.exe> --timeout 120
```

`--output-dir` is required with `--simulate` and must not already exist. Simulation mode writes
`design.cir`, `design.asc`, `circuit-ir.json`, `request.json`, `bom.json`, LTspice `design.raw`
and `design.log` when produced, and `request-verification.json`. The CIR and ASC are generated
from the same synthesized `CircuitIR`. When conditions are specified, that IR includes the
requested source and load resistances; requests omitting the object keep the ideal-source/open-
load circuit. Verification estimates low-frequency passband gain from complex LTspice AC samples:
for a one-pole transfer `H = G0/(1+jωτ)`, `real(1/H)` estimates `1/G0`; the median across sweep
points is inverted to estimate `G0`. It then measures cutoff at the descending -3 dB point
relative to that estimated passband.
It checks the inclusive cutoff range and, when requested, checks the passband-gain range as a
separate hard constraint. Analytical candidate feasibility remains separately labeled; it cannot
make measured checks pass.
The simulation mode replaces the default fixed AC directive in the returned `CircuitIR` with a
100-points-per-decade sweep from one decade below to one decade above the selected candidate's
analytical cutoff. The candidate is bracketed by that sweep, including requests above 1 MHz or
below 1 Hz. The adapted IR is used for both `design.cir` and `design.asc`, and the sweep bounds
are recorded in the verification report.
This asymptote estimate depends on the one-pole model used by this topology; it is not a general
multi-pole passband estimator. The corrected loaded-condition real run below uses this estimator.
The earlier loaded run using the old sweep-start estimate is superseded and is documented below
as a diagnostic; run2 is the corrected real loaded-condition evidence.

Failed launches, timeouts, missing raw data, missing/nonpositive passband estimates, missing
cutoff crossings, or measured values outside a requested range produce `design_pass: false` and
a nonzero CLI exit. The report keeps
`production_qualified` false. This request-run mode does not alter the direct-value RC command or
the six example design workflows.

## Bounded conventional refinement

To search multiple E-series pairs, combine `--refine` with `--simulate` and supply an explicit
positive simulation budget:

```powershell
python -m circuitpilot.design_request_cli --request docs/example-rc-lowpass-request.json `
  --simulate --refine --budget 5 --output-dir output/<new-refinement-run> `
  --ltspice <path-to-LTspice.exe> --timeout 120
```

Refinement considers every R/C E-series pair inside the requested component bounds, including
pairs whose analytical cutoff is outside the requested range. Candidates are ordered by
requested passband-gain feasibility first when a gain range exists, then by absolute log-distance
from the requested cutoff target or geometric range center, gain-range midpoint proximity, and
finally lower resistance and lower capacitance. Each pair is
synthesized internally from the same validated request and simulated in `candidate-001`,
`candidate-002`, and so on. No externally supplied candidate can be paired with a different
request.

The search stops on the first measured cutoff inside the inclusive range, on the simulation
budget, or when the candidate space is exhausted. A simulator launch, timeout, missing raw file,
or missing cutoff crossing stops refinement immediately as a failure; an actual measured
out-of-range cutoff advances to the next candidate. The new output root contains each attempted
candidate's CIR, ASC, IR, request, BOM, raw/log when produced, and verification report, plus
top-level ordered `refinement-history.json` and `refinement-report.json`. Both top-level reports
record status, stop reason, and simulations used. A failed search exits nonzero and retains the
attempt evidence. All reports keep `production_qualified` false.

`docs/example-rc-lowpass-loaded-request.json` demonstrates finite source/load resistances and a
passband-gain range. To run it in a fresh folder:

```powershell
python -m circuitpilot.design_request_cli --request docs/example-rc-lowpass-loaded-request.json `
  --simulate --output-dir output/<new-loaded-request-run> --ltspice <path-to-LTspice.exe> --timeout 120
```

The saved LTspice refinement runs below use ideal-source/open-load requests; the separate loaded
request run above verifies nominal finite source/load conditions and a passband-gain range.

### Recorded loaded-condition LTspice run

LTspice 26.1.1.0 completed the loaded example request at
`output/rc_behavior_loaded_20261024_run2`. For Rs=100 Ω and RL=10 kΩ, its 201 AC points gave a
complex-asymptote passband estimate of 0.900900900900901, within the requested 0.89–0.92 range,
and a measured cutoff of 1606.0180621091863 Hz, within the inclusive 1500–1700 Hz range. Both
hard checks passed. The analytical candidate values were 1606.0180621091279 Hz and
0.9009009009009008 gain. The saved verification report keeps `production_qualified: false`.

`output/rc_behavior_loaded_20261024_run1` is a preserved superseded diagnostic from the old
sweep-start method. It reported 1621.894563077167 Hz and a gain estimate of 0.8964299010900756;
those values are biased and are not verification evidence. Use run2 for the corrected loaded
measurement.

### Recorded LTspice refinement evidence

LTspice 26.1.1.0 completed two RC cutoff refinements. The example request run at
`output/rc_behavior_refinement_20261024_run1` had a budget of 5 and stopped after one candidate;
the measured 1591.5494309190115 Hz cutoff passed the inclusive 1500–1700 Hz range.

The two-candidate case uses the tracked request
[`example-rc-lowpass-two-candidate-request.json`](example-rc-lowpass-two-candidate-request.json)
and is saved at
`output/rc_behavior_refinement_20261024_two_candidate_run1`. Candidate 1 (1000 Ω, 100 nF)
measured 1591.5494309190115 Hz and failed the 1300–1500 Hz range. Candidate 2 (1000 Ω,
120 nF) measured 1326.2911924324903 Hz and passed; refinement stopped with
`first_measured_pass` after 2 simulations. Each attempt has its own CIR, ASC, IR, raw/log, and
verification report. These results cover only the requested RC cutoff checks, and both runs
retain `production_qualified: false`.

To repeat the search, use a new output folder:

```powershell
python -m circuitpilot.design_request_cli --request docs/example-rc-lowpass-two-candidate-request.json `
  --simulate --refine --budget 2 --output-dir output/<new-two-candidate-refinement-run> `
  --ltspice <path-to-LTspice.exe> --timeout 120
```

The existing `python -m circuitpilot --r ... --c ...` command remains the direct-value RC
calculator. The behavioral request path is separate so that its contract does not change the
older command or the existing amplifier design request format.
