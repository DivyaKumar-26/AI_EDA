# Design requirements template

Use this template to make the design target explicit before simulation. This project currently accepts only the six `design_id` values listed below; it does not infer requirements from prose. The machine-readable format is validated by the engine and defined in [`schemas/design-requirements.schema.json`](../schemas/design-requirements.schema.json).

## Fill-in checklist

- Circuit example ID: one of `common-source-mosfet-amplifier`, `common-emitter-bjt-amplifier`, `inverting-opamp-amplifier`, `non-inverting-opamp-amplifier`, `mosfet-bias-network`, or `bjt-bias-network`.
- Supply type and nominal magnitude: single rail or symmetric dual rail. The current six transistor examples use a 12 V single rail; the op-amp examples use ±15 V dual rails as the reference design basis.
- Supply tolerance, exactly three ambient temperature points from 0 to 70 °C, resistor tolerance, capacitor tolerance, source resistance, source amplitude/frequency, output load, maximum output peak, and maximum THD estimate. The runner applies transistor nominal supply, amplifier source resistance, and resistive amplifier load. Temperature/tolerance corners and transient amplitude, output swing, and THD are not simulated yet; reports mark these conditions incomplete.
- At least one hard requirement. Enter a measurement key supported for that topology, its exact unit, and inclusive minimum and maximum.
- Add all metrics that matter for the application. A requirement missing its measurement, having the wrong unit, or outside its declared bounds fails closed.

## Copy/paste request prompt

Fill every field you know. Leave unknown fields as `[unknown]`; do not invent a value. This prompt is a requirements-gathering aid only. CircuitPilot does not parse natural-language requests yet. Translate the completed answers into the validated JSON format below before running a design.

```text
Design request

Circuit or application: [fill in]
Desired circuit function / topology: [fill in]
Transfer function or behavior: [fill in]
Required gain and reference frequency: [fill in, with units]
Required lower cutoff, upper cutoff, bandwidth, or settling time: [fill in, with units]
Supply type, nominal voltage, and tolerance: [fill in]
Input signal: [waveform, amplitude, frequency range, source resistance]
Output load: [resistance/capacitance and connection]
Ambient temperature points: [three points from 0 to 70 °C for the current engine]
Resistor and capacitor tolerance: [fill in]
Maximum output swing / clipping allowance: [fill in]
Maximum distortion: [fill in; identify whether an estimate is acceptable]
Noise requirement and measurement bandwidth: [fill in or unknown]
Allowed or preferred parts / forbidden parts: [fill in]
Component, board, or power budget: [fill in or unknown]
Required deliverables: [ASC, CIR, BOM, measured report, native netlist comparison, other]
Required simulator evidence and acceptance limits: [fill in]
Other constraints: [fill in]
```

## Machine-readable example

This is a fill-in example for the inverting op-amp. It demonstrates schema shape and units; its illustrative limits are not a validated product specification and the example must be simulated against the selected device before use.

```json
{
  "schema_version": 1,
  "design_id": "inverting-opamp-amplifier",
  "operating_conditions": {
    "supply_mode": "dual",
    "nominal_supply_v": 15.0,
    "supply_tolerance_percent": 10.0,
    "ambient_temperature_c": [0.0, 27.0, 70.0],
    "resistor_tolerance_percent": 1.0,
    "capacitor_tolerance_percent": 10.0,
    "load_resistance_ohm": 10000.0,
    "source_resistance_ohm": 50.0,
    "input_amplitude_v": 0.01,
    "input_frequency_hz": 1000.0,
    "maximum_output_peak_v": 10.0,
    "maximum_thd_percent": 5.0
  },
  "requirements": [
    {
      "metric": "gain_at_1khz_magnitude_v_per_v",
      "unit": "V/V",
      "min": 9.5,
      "max": 10.5
    },
    {
      "metric": "minus_3db_high_cutoff_hz",
      "unit": "Hz",
      "min": 20000.0,
      "max": 120000.0
    }
  ]
}
```

Run an example design against a specification with:

```powershell
$env:PYTHONPATH = "engine"
python -m circuitpilot.examples_cli --design inverting-opamp-amplifier --spec docs/example-inverting-opamp-spec.json --output-root output/my-opamp-run
```

The engine checks declared measurement bounds on nominal CIR simulation results. Pass the same ID with `--design`; the CLI requires one design matching the spec. Nominal supply for transistor designs, source resistance, and resistive amplifier load are inserted into the generated CIR and recorded in its BOM. Missing/non-finite measured values, unsupported metrics, incompatible units, non-finite limits, invalid ranges, missing fields, unknown design IDs, and extra fields fail closed. Reports distinguish `design_basis: user_requirements` from `design_basis: reference_design_defaults` and keep `production_qualified` false. The overall spec run remains incomplete until temperature/tolerance corners and transient conditions are simulated. A simulation result alone cannot establish hardware or production qualification.

## Supported metric keys and units

| Metric | Unit | Topology availability |
|---|---|---|
| `gain_at_1khz_magnitude_v_per_v` | `V/V` | All four amplifier examples |
| `gain_at_1khz_db`, `peak_gain_db` | `dB` | All four amplifier examples |
| `peak_gain_magnitude_v_per_v` | `V/V` | All four amplifier examples |
| `phase_at_1khz_degrees` | `deg` | All four amplifier examples |
| `minus_3db_low_cutoff_hz`, `minus_3db_high_cutoff_hz`, `bandwidth_hz` | `Hz` | All four amplifier examples; absent crossings fail if required |
| `bias.vgs_v`, `bias.vds_v`, `bias.drain_current_a_estimate` | `V`, `V`, `A` | MOSFET amplifier |
| `bias.vbe_v`, `bias.vce_v`, `bias.collector_current_a_estimate` | `V`, `V`, `A` | BJT amplifier |
| `nominal_bias.vgs_v`, `nominal_bias.vds_v`, `nominal_bias.drain_current_a_estimate` | `V`, `V`, `A` | MOSFET bias network |
| `nominal_bias.vbe_v`, `nominal_bias.vce_v`, `nominal_bias.collector_current_a_estimate` | `V`, `V`, `A` | BJT bias network |
| `nominal_supply_v` | `V` | MOSFET and BJT bias networks |
| `sweep_points`, `sweep_summary.active_region_points`, `sweep_summary.total_points` | `count` | MOSFET and BJT bias networks |
| `sweep_summary.min_current_a`, `sweep_summary.max_current_a` | `A` | MOSFET and BJT bias networks |

Transient metrics such as `transient.gain_v_per_v`, `transient.output_peak_v`,
`transient.clipping_margin_v`, `transient.rail_headroom_v`, and
`transient.thd_percent` are planned but are not currently accepted as hard requirements.
The validator rejects them as unsupported metrics until the engine measures them.

The accepted measurements are versioned in `engine/circuitpilot/requirements.py`; the run report records each bound, unit, observed value, and pass/fail result.
