# Plain-language behavioral request intake

AI_EDA has a deterministic, offline phrase parser for the existing behavioral request topologies. It converts a complete supported sentence into the same validated JSON request used by `--request`; that request then follows the existing synthesis, simulation, or RC low-pass refinement path. No LLM, network service, or extra dependency is used.

This is a bounded grammar, not general natural-language understanding. State one topology, one requested behavior range, and every required component range explicitly. The parser returns clarification questions for missing fields, rejects ambiguous duplicate ranges and unsupported conditions, and never fills electrical values by inference.

## Sentence format

Start with `Design a/an <topology>` and follow with the named behavior and component ranges. Range endpoints require units; each component range requires an E-series (`E3`, `E6`, `E12`, or `E24`). Component clauses can be joined with commas and `and`. A target may be included as `target <value-unit>`.

Supported topology phrases and their required information:

| Topology phrase | Behavior range | Required component ranges |
| --- | --- | --- |
| `RC low-pass filter` | `cutoff` | resistance, capacitance |
| `RC high-pass filter` | `cutoff` | resistance, capacitance |
| `RL low-pass filter` | `cutoff` | resistance, inductance |
| `op-amp buffered RC low-pass filter` or `buffered RC low-pass filter` | `cutoff` | resistance, capacitance |
| `series RLC band-pass filter` | `resonance` | resistance, capacitance, inductance |
| `inverting op-amp amplifier` | `gain magnitude` | input resistance, feedback resistance |

Examples:

```text
Design an RC low-pass filter with cutoff from 1.5 kHz to 1.7 kHz, resistance from 1 kOhm to 10 kOhm using E12, and capacitance from 50 nF to 150 nF using E12.
Design an RC high-pass filter with cutoff between 1.5 kHz and 1.7 kHz, resistance between 1 kOhm and 10 kOhm using E12, and capacitance between 50 nF and 150 nF using E12.
Design an RL low-pass filter with cutoff from 1.5 kHz to 1.7 kHz, resistance from 1 kOhm to 10 kOhm using E12, and inductance from 50 mH to 150 mH using E12.
Design an op-amp buffered RC low-pass filter with cutoff from 1.5 kHz to 1.7 kHz, resistance from 1 kOhm to 10 kOhm using E12, and capacitance from 50 nF to 150 nF using E12.
Design a series RLC band-pass filter with resonance from 1.5 kHz to 1.7 kHz, resistance from 100 Ohm to 1 kOhm using E12, capacitance from 50 nF to 150 nF using E12, and inductance from 50 mH to 150 mH using E12.
Design an inverting op-amp amplifier with gain magnitude from 2 V/V to 10 V/V, input resistance from 1 kOhm to 10 kOhm using E12, and feedback resistance from 2 kOhm to 100 kOhm using E12.
```

Frequency units: Hz, kHz, MHz. Resistance units: ohm/Ω and kohm/kΩ. Capacitance units: F, mF, uF/µF, nF, pF. Inductance units: H, mH, uH/µH, nH. Gain magnitude uses `V/V`. Values must be positive and satisfy the existing topology validator, including topology-specific limits such as op-amp bandwidth and gain bounds. The current handlers use their ideal-source, open-load reference model. Source/load conditions, tolerances, temperature corners, additional metrics, and behavior outside these supported request contracts must be specified through another supported interface or are rejected.

## Legacy labeled format

The original semicolon-labeled syntax remains supported for RC low-pass requests:

```text
RC low-pass; cutoff: 1.5 kHz to 1.7 kHz; target: 1.59155 kHz; resistance: 1 kOhm to 10 kOhm E12; capacitance: 50 nF to 150 nF E12
```

It continues to use the existing RC low-pass validator and reports its canonical ideal-source/open-load assumption.

## CLI

Pass either sentence or the legacy labeled request as one shell-quoted `--prompt` argument:

```powershell
$env:PYTHONPATH = 'engine'
python -m circuitpilot.design_request_cli --prompt "Design an RC high-pass filter with cutoff from 1.5 kHz to 1.7 kHz, resistance from 1 kOhm to 10 kOhm using E12, and capacitance from 50 nF to 150 nF using E12."
```

The JSON output includes the original prompt, normalized validated request, assumptions, generated CircuitIR/netlist/ASC, and synthesis report. Incomplete or unsupported prompts emit an `intake_report` and exit with status 2. Complete prompts can use the existing simulation flow:

```powershell
python -m circuitpilot.design_request_cli --prompt "Design an RC low-pass filter with cutoff from 1.5 kHz to 1.7 kHz, resistance from 1 kOhm to 10 kOhm using E12, and capacitance from 50 nF to 150 nF using E12." --simulate --output-dir output/plain_language_rc_smoke --timeout 120
```

Simulation still requires LTspice and a new output directory. Only RC low-pass supports `--refine`. Prompt parsing and analytical synthesis do not claim simulation or production qualification.
