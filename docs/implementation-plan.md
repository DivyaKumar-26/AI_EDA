# CircuitPilot implementation plan

## Current implementation status

The initial implementation now has a Python `CircuitIR`, deterministic SPICE rendering, strict topology registration/schema validation, an LTspice batch adapter and ASCII raw-data measurements. It includes the original RC low-pass plus six netlist-backed examples: common-source MOSFET, common-emitter BJT, inverting and non-inverting behavioral op amp, MOSFET bias, and BJT bias. The examples are intentionally bounded templates with generic/self-contained models, not arbitrary analog synthesis or product-ready designs. No `.asc` exporter exists yet.

The first RL experiment is implemented only for RC low-pass tuning on a 3×3 discrete R/C lattice and one fixed target. LTspice first measures every candidate. A dependency-free tabular Q-learning policy then trains and evaluates over that measured table. This demonstrates a reusable environment/reward loop and checkpoint format, but does not establish target-range or cross-topology generalization. Benchmark artifacts must be generated and retained from an actual run before quoting performance.

## Goal

Build a circuit tool that can reliably simulate and verify a design first. Then add an optional reinforcement-learning (RL) policy that learns which design change to try next. LTspice supplies measurements; the verification engine remains the authority on whether requirements pass.

## What to learn from LTSPICE-AI

The reference project demonstrates a useful workflow: create a SPICE netlist, run LTspice, read its `.raw` results, and make those results visible. CircuitPilot can use that workflow as a starting point. Its RL policy is a separate later layer; the reference project is not itself an RL design system. [Reference repository](https://github.com/kongpop10/LTSPICE-AI/tree/master)

## Build in these steps

| Step | What we build | Done when |
|---|---|---|
| 1. Make one circuit work | **Implemented:** validated RC netlist, analytical estimate, optional LTspice execution, measured cutoff, saved report and safe failure handling. | Unit tests plus actual LTspice simulation artifacts. |
| 2. Define a clear design goal | **Partial:** topology parameters have validated schemas; arbitrary requirement/tolerance specification and hard-constraint engine remain future work. | Invalid topology parameters are rejected before synthesis. |
| 3. Establish a non-RL baseline | **Partial:** the fixed-grid experiment records an exhaustive-grid oracle and random-walk baseline. No conventional continuous optimizer benchmark yet. | Baselines and measured simulator-call count are in the benchmark artifact. |
| 4. Turn tuning into an environment | **Implemented for RC:** typed one-grid-step actions and bounded state transitions over a measured candidate table. | Environment tests cover transitions, reward and reproducibility. |
| 5. Define the learning signal | **Implemented for RC v1:** log-relative cutoff-error improvement, step cost, and within-tolerance reward use measured cutoffs. | Training table contains LTspice measurement data; policy training replays it. |
| 6. Train on many RC tasks | **Not implemented:** v1 uses one target and one fixed grid. | Requires randomized target/range generation and held-out tasks. |
| 7. Compare fairly | **Narrow v1 only:** compares learned policy against a random walk on identical states plus an exhaustive oracle bound. | Does not support generalization claims. |
| 8. Add more circuit families | **Partial:** six LTspice example netlists run through shared rendering/simulation/measurement seams; no RL actions or optimizer for them. | Each example has focused tests and simulation report. |
| 9. Offer RL as an optional helper | Future product integration. | No VS Code UI or learned policy deployment yet. |

## First RL task

Current v1 uses only the RC low-pass topology, fixed target, and a pre-measured 3×3 R/C grid. The state is the grid location; actions move one R or C index by one step. The observation does not encode arbitrary targets or simulation budget, and action steps replay precomputed LTspice measurements rather than launching a new simulation. This makes training reproducible and fast after data acquisition, while limiting what the experiment can prove. A future online/generalized environment must add explicit target and budget observations before making those claims.

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
