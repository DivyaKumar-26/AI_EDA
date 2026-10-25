"""Small dispatch and handlers for additional first-order behavioral topologies."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import subprocess
from typing import Any, Callable, Mapping

from .ir import CircuitElement, CircuitIR
from .rc_request import NoFeasibleCandidateError, RCSynthesisResult, validate_rc_request
from .rc_lowpass import format_spice_number
from .designs import _OPAMP
from .schematic import render_asc, required_support_files
from .simulation import (SimulationError, estimate_one_pole_passband_gain,
                         measure_cutoff_relative_to_passband, measure_gain_magnitude_at_frequency,
                         measure_peak_frequency_hz,
                         parse_ascii_ac_raw, run_spice)


_VALUES = {
    "E3": (1.0, 2.2, 4.7),
    "E6": (1.0, 1.5, 2.2, 3.3, 4.7, 6.8),
    "E12": (1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2),
    "E24": (1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0,
            3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1),
}
_COMPONENTS = {
    "rc_highpass": ("resistance_ohm", "capacitance_f", 1e-6, 1e12, 1e-15, 1e3),
    "rl_lowpass": ("resistance_ohm", "inductance_h", 1e-6, 1e12, 1e-12, 1e9),
    "opamp_rc_lowpass": ("resistance_ohm", "capacitance_f", 1e-6, 1e12, 1e-15, 1e3),
}
_RLC_TOPOLOGY = "series_rlc_bandpass"
_OPAMP_RC_MAX_CUTOFF_HZ = 150_000.0
_INVERTING_OPAMP_TOPOLOGY = "inverting_opamp_amplifier"
_INVERTING_OPAMP_MAX_GAIN = 100.0
_SALLEN_KEY_TOPOLOGY = "sallen_key_butterworth_lowpass"
_SALLEN_KEY_MAX_CUTOFF_HZ = 150_000.0
_SALLEN_KEY_Q_RELATIVE_TOLERANCE = 0.05
_SALLEN_KEY_MAX_CANDIDATES = 1_000_000


@dataclass(frozen=True)
class TopologyRequestResult:
    circuit_ir: CircuitIR
    report: dict


def load_behavioral_request(path: str | Path) -> dict:
    """Load one supported behavioral request through a topology-specific validator."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read valid JSON request: {exc}") from exc
    return validate_behavioral_request(raw)


def validate_behavioral_request(request: Any) -> dict:
    """Validate and normalize an in-memory request for any currently supported topology."""
    if isinstance(request, dict) and request.get("topology") == "rc_lowpass":
        return validate_rc_request(request)
    return validate_topology_request(request)


def validate_topology_request(request: Any) -> dict:
    if not isinstance(request, dict):
        raise ValueError("request must be an object")
    topology = request.get("topology")
    if not isinstance(topology, str) or (topology not in _COMPONENTS and topology not in (_RLC_TOPOLOGY, _INVERTING_OPAMP_TOPOLOGY, _SALLEN_KEY_TOPOLOGY)):
        raise ValueError("topology must be one of the supported RC/RL, op-amp, series_rlc_bandpass, or sallen_key_butterworth_lowpass values")
    if set(request) != {"schema_version", "topology", "behavior", "components"}:
        raise ValueError("request must contain only schema_version, topology, behavior, and components")
    if request["schema_version"] != 1 or isinstance(request["schema_version"], bool):
        raise ValueError("schema_version must be 1")
    if topology == _RLC_TOPOLOGY:
        return _validate_series_rlc_request(request)
    if topology == _INVERTING_OPAMP_TOPOLOGY:
        return _validate_inverting_opamp_request(request)
    if topology == _SALLEN_KEY_TOPOLOGY:
        return _validate_sallen_key_request(request)
    behavior = request["behavior"]
    if not isinstance(behavior, dict) or set(behavior) != {"cutoff_hz"}:
        raise ValueError("behavior must contain only cutoff_hz")
    cutoff = behavior["cutoff_hz"]
    if not isinstance(cutoff, dict) or set(cutoff) - {"min", "max", "target"} or not {"min", "max"} <= set(cutoff):
        raise ValueError("cutoff_hz requires min/max and optionally target")
    low = _positive(cutoff["min"], "cutoff_hz.min")
    high = _positive(cutoff["max"], "cutoff_hz.max")
    if low > high:
        raise ValueError("cutoff_hz.min must be <= max")
    if topology == "opamp_rc_lowpass" and high > _OPAMP_RC_MAX_CUTOFF_HZ:
        raise ValueError(
            "opamp_rc_lowpass cutoff_hz.max must be <= 150000 Hz; the CP_OPAMP follower has "
            "about 15.9 MHz gain-bandwidth, so this topology limits RC cutoff to under 1% of it"
        )
    target = _positive(cutoff["target"], "cutoff_hz.target") if "target" in cutoff else None
    if target is not None and not low <= target <= high:
        raise ValueError("cutoff_hz.target must be within min/max")
    resistance_name, second_name, rmin, rmax, x_min, x_max = _COMPONENTS[topology]
    components = request["components"]
    if not isinstance(components, dict) or set(components) != {resistance_name, second_name}:
        raise ValueError(f"components must contain {resistance_name} and {second_name}")
    normalized = {}
    for name, physical_min, physical_max in ((resistance_name, rmin, rmax), (second_name, x_min, x_max)):
        bounds = components[name]
        if not isinstance(bounds, dict) or set(bounds) != {"min", "max", "series"}:
            raise ValueError(f"components.{name} requires min, max, and series")
        component_low = _positive(bounds["min"], f"components.{name}.min")
        component_high = _positive(bounds["max"], f"components.{name}.max")
        if component_low > component_high or component_low < physical_min or component_high > physical_max:
            raise ValueError(f"components.{name} bounds are reversed or outside supported physical limits")
        series = bounds["series"]
        if not isinstance(series, str) or series.upper() not in _VALUES:
            raise ValueError(f"components.{name}.series must be E3, E6, E12, or E24")
        normalized[name] = {"min": component_low, "max": component_high, "series": series.upper()}
    norm_cutoff = {"min": low, "max": high}
    if target is not None:
        norm_cutoff["target"] = target
    return {"schema_version": 1, "topology": topology, "behavior": {"cutoff_hz": norm_cutoff},
            "components": normalized}


def synthesize_behavioral_request(request: Mapping[str, Any]) -> RCSynthesisResult | TopologyRequestResult:
    topology = request.get("topology") if isinstance(request, Mapping) else None
    if topology == "rc_lowpass":
        return synthesize_rc_request_dispatch(request)
    return synthesize_topology_request(request)


def synthesize_rc_request_dispatch(request: Mapping[str, Any]) -> RCSynthesisResult:
    # Kept here so the CLI's topology dispatch leaves the established RC LP pipeline intact.
    from .rc_request import synthesize_rc_request
    return synthesize_rc_request(request)


def synthesize_topology_request(request: Mapping[str, Any]) -> TopologyRequestResult:
    spec = validate_topology_request(dict(request))
    topology = spec["topology"]
    if topology == _RLC_TOPOLOGY:
        return _synthesize_series_rlc(spec)
    if topology == _INVERTING_OPAMP_TOPOLOGY:
        return _synthesize_inverting_opamp(spec)
    if topology == _SALLEN_KEY_TOPOLOGY:
        return _synthesize_sallen_key(spec)
    resistance_name, second_name, *_ = _COMPONENTS[topology]
    r_values = _standard_values(spec["components"][resistance_name])
    x_values = _standard_values(spec["components"][second_name])
    cutoff_bounds = spec["behavior"]["cutoff_hz"]
    requested_target = cutoff_bounds.get("target")
    log_target = (
        math.log(requested_target) if requested_target is not None
        else (math.log(cutoff_bounds["min"]) + math.log(cutoff_bounds["max"])) / 2.0
    )
    target = math.exp(log_target)
    candidates = []
    for resistance in r_values:
        for reactance in x_values:
            cutoff = (1.0 / (2.0 * math.pi * resistance * reactance)
                      if topology in ("rc_highpass", "opamp_rc_lowpass")
                      else resistance / (2.0 * math.pi * reactance))
            if not math.isfinite(cutoff) or cutoff <= 0:
                continue
            candidates.append({resistance_name: resistance, second_name: reactance, "cutoff_hz": cutoff})
    candidates.sort(key=lambda item: (abs(math.log(item["cutoff_hz"]) - log_target),
                                      item[resistance_name], item[second_name]))
    feasible = [item for item in candidates if cutoff_bounds["min"] <= item["cutoff_hz"] <= cutoff_bounds["max"]]
    report = {
        "schema_version": 1,
        "topology": topology,
        "status": "feasible" if feasible else "infeasible",
        "constraint_evidence": "analytical",
        "verification_status": "not_simulated",
        "production_qualified": False,
        "hard_constraints": [{"name": "cutoff_hz", "min": cutoff_bounds["min"], "max": cutoff_bounds["max"],
                              "pass": bool(feasible), "evidence": "analytical"}],
        "candidate_search": {"component_pairs_considered": len(r_values) * len(x_values),
                             "feasible_candidate_count": len(feasible),
                             "ranking_reference_hz": target,
                             "ranking_method": "closest in log frequency to target or geometric range center"},
        "candidate": feasible[0] if feasible else None,
    }
    if not feasible:
        raise NoFeasibleCandidateError(report)
    candidate = feasible[0]
    r = candidate[resistance_name]
    x = candidate[second_name]
    subcircuits = ()
    if topology == "rc_highpass":
        elements = (CircuitElement.from_statement("V1 in 0 AC 1"),
                    CircuitElement.from_statement(f"C1 in out {format_spice_number(x)}"),
                    CircuitElement.from_statement(f"R1 out 0 {format_spice_number(r)}"))
        title = "CircuitPilot first-order RC high-pass"
    elif topology == "opamp_rc_lowpass":
        elements = (CircuitElement.from_statement("V1 in 0 AC 1"),
                    CircuitElement.from_statement(f"R1 in filt {format_spice_number(r)}"),
                    CircuitElement.from_statement(f"C1 filt 0 {format_spice_number(x)}"),
                    CircuitElement.from_statement("XU1 filt out out CP_OPAMP"))
        subcircuits = (_OPAMP.strip(),)
        title = "CircuitPilot op-amp buffered first-order RC low-pass"
    else:
        elements = (CircuitElement.from_statement("V1 in 0 AC 1"),
                    CircuitElement.from_statement(f"L1 in out {format_spice_number(x)}"),
                    CircuitElement.from_statement(f"R1 out 0 {format_spice_number(r)}"))
        title = "CircuitPilot first-order RL low-pass"
    circuit = CircuitIR(title, elements, subcircuits=subcircuits,
                        directives=(".ac dec 100 1 1Meg",))
    return TopologyRequestResult(circuit, report)


def simulate_topology_request(
    request: Mapping[str, Any], output_dir: str | Path, executable: str | None = None,
    timeout_seconds: float = 120.0, runner: Callable = subprocess.run,
) -> tuple[TopologyRequestResult, dict]:
    """Run a new-topology candidate and verify its measured cutoff in the proper direction."""
    spec = validate_topology_request(dict(request))
    result = synthesize_topology_request(spec)
    topology = spec["topology"]
    is_rlc = topology == _RLC_TOPOLOGY
    is_opamp = topology == "opamp_rc_lowpass"
    is_inverting_opamp = topology == _INVERTING_OPAMP_TOPOLOGY
    is_sallen_key = topology == _SALLEN_KEY_TOPOLOGY
    direction = "ascending" if topology == "rc_highpass" else "descending"
    metric_name = "resonance_hz" if is_rlc else ("gain_magnitude" if is_inverting_opamp else "cutoff_hz")
    expected_frequency = 1000.0 if is_inverting_opamp else result.report["candidate"][metric_name]
    low, high = ((10.0, 100_000_000.0) if is_inverting_opamp else
                 (expected_frequency / 100.0, expected_frequency * 20.0) if is_sallen_key else
                 (expected_frequency / 10.0, expected_frequency * 10.0))
    directives = tuple(f".ac dec 100 {format_spice_number(low)} {format_spice_number(high)}"
                       if line.casefold().startswith(".ac ") else line
                       for line in result.circuit_ir.directives)
    result = TopologyRequestResult(CircuitIR(
        result.circuit_ir.title, result.circuit_ir.elements, models=result.circuit_ir.models,
        subcircuits=result.circuit_ir.subcircuits, directives=directives,
        comments=result.circuit_ir.comments,
    ), result.report)
    directory = Path(output_dir).expanduser().resolve()
    if directory.exists():
        raise SimulationError(f"Refusing to overwrite existing run artifacts in {directory}")
    directory.mkdir(parents=True)
    (directory / "circuit-ir.json").write_text(json.dumps(result.circuit_ir.to_dict(), indent=2) + "\n",
                                                encoding="utf-8")
    (directory / "request.json").write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (directory / "design.asc").write_text(render_asc(result.circuit_ir, sheet_title=result.circuit_ir.title),
                                           encoding="utf-8")
    support_files = required_support_files(result.circuit_ir)
    for filename, content in support_files.items():
        (directory / filename).write_text(content, encoding="ascii", newline="\n")
    bounds = spec["behavior"]["resonance_hz" if is_rlc else
                            ("gain_magnitude" if is_inverting_opamp else "cutoff_hz")]
    try:
        batch = run_spice(result.circuit_ir, "design", directory, executable, timeout_seconds, runner)
        points = parse_ascii_ac_raw(batch.raw_path.read_text(encoding="ascii", errors="replace"),
                                    input_name="V(in)", output_name="V(out)")
        if is_rlc:
            measured = measure_peak_frequency_hz(points)
            measurements = {"resonance_hz": measured, "ac_points": len(points),
                            "evidence": "LTspice ASCII AC raw data"}
        elif is_inverting_opamp:
            measured = measure_gain_magnitude_at_frequency(points, 1000.0)
            measurements = {"gain_magnitude": measured, "frequency_hz": 1000.0,
                            "ac_points": len(points), "evidence": "LTspice ASCII AC raw data"}
        elif is_sallen_key:
            passband_gain = measure_gain_magnitude_at_frequency(points, points[0].frequency_hz)
            measured = measure_cutoff_relative_to_passband(points, passband_gain, direction="descending")
            attenuation_frequency = 10.0 * measured
            attenuation_gain = measure_gain_magnitude_at_frequency(points, attenuation_frequency)
            attenuation_db = 20.0 * math.log10(attenuation_gain / passband_gain)
            measurements = {"cutoff_hz": measured,
                            "passband_gain_estimate_at_sweep_start": passband_gain,
                            "attenuation_at_10x_cutoff_db": attenuation_db,
                            "attenuation_measurement_frequency_hz": attenuation_frequency,
                            "ac_points": len(points), "evidence": "LTspice ASCII AC raw data"}
        else:
            passband_gain = estimate_one_pole_passband_gain(points)
            measured = measure_cutoff_relative_to_passband(points, passband_gain, direction=direction)
            measurements = {"cutoff_hz": measured,
                            "passband_gain_estimate_from_complex_ac": passband_gain,
                            "ac_points": len(points), "evidence": "LTspice ASCII AC raw data"}
        passed = bounds["min"] <= measured <= bounds["max"]
        report = {
            "topology": topology,
            "simulation_status": "completed",
            "verification_status": "passed" if passed else "failed",
            "design_pass": passed,
            "production_qualified": False,
            **({} if is_rlc or is_inverting_opamp else {"cutoff_direction": "descending" if is_sallen_key else direction}),
            "measurement_method": ("interpolated AC output-magnitude peak" if is_rlc else
                                   "|V(out)/V(in)| at 1 kHz" if is_inverting_opamp else
                                   "descending -3 dB crossing relative to sweep-start passband estimate" if is_sallen_key else
                                   direction + " -3 dB crossing"),
            ("requested_resonance_hz" if is_rlc else
             "requested_gain_magnitude" if is_inverting_opamp else
             "requested_cutoff_hz"): {"min": bounds["min"], "max": bounds["max"]},
            "measurements": measurements,
            "hard_constraints": [{"name": metric_name, "min": bounds["min"], "max": bounds["max"],
                                  "measured": measured, "pass": passed,
                                  "evidence": "simulation_measurement"}],
            "simulator": {"name": "LTspice", "executable": batch.simulator_executable},
            "artifacts": {"netlist": "design.cir", "schematic": "design.asc", "raw": "design.raw",
                          "log": "design.log", "circuit_ir": "circuit-ir.json", "request": "request.json",
                          "support_files": sorted(support_files)},
            "ac_sweep_hz": {"min": low, "max": high,
                             "expected_resonance_hz" if is_rlc else
                             "measurement_frequency_hz" if is_inverting_opamp else
                             "expected_cutoff_hz": expected_frequency},
        }
    except (OSError, SimulationError) as exc:
        report = {
            "topology": topology, "simulation_status": "failed", "verification_status": "failed",
            "design_pass": False, "production_qualified": False,
            **({} if is_rlc or is_inverting_opamp else {"cutoff_direction": "descending" if is_sallen_key else direction}),
            ("requested_resonance_hz" if is_rlc else
             "requested_gain_magnitude" if is_inverting_opamp else
             "requested_cutoff_hz"): {"min": bounds["min"], "max": bounds["max"]},
            "measurements": ({"resonance_hz": None} if is_rlc else
                             {"gain_magnitude": None, "frequency_hz": 1000.0} if is_inverting_opamp else
                             {"cutoff_hz": None, "passband_gain_estimate_at_sweep_start": None} if is_sallen_key else
                             {"cutoff_hz": None, "passband_gain_estimate_from_complex_ac": None}),
            "hard_constraints": [{"name": metric_name, "min": bounds["min"], "max": bounds["max"],
                                  "measured": None, "pass": False,
                                  "evidence": "missing_simulation_measurement"}],
            "failure_kind": "simulation_or_measurement_failed", "error": str(exc),
            "artifacts_present": {name: (directory / name).is_file()
                                  for name in ("design.cir", "design.raw", "design.log", *support_files)},
            "ac_sweep_hz": {"min": low, "max": high,
                             "expected_resonance_hz" if is_rlc else
                             "measurement_frequency_hz" if is_inverting_opamp else
                             "expected_cutoff_hz": expected_frequency},
        }
    (directory / "request-verification.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                                                           encoding="utf-8")
    return result, report


def _standard_values(bounds: Mapping[str, Any]) -> list[float]:
    low, high = bounds["min"], bounds["max"]
    first_decade = math.floor(math.log10(low)) - 1
    last_decade = math.floor(math.log10(high)) + 1
    return sorted({base * 10.0 ** decade for decade in range(first_decade, last_decade + 1)
                   for base in _VALUES[bounds["series"]] if low <= base * 10.0 ** decade <= high})


def _validate_sallen_key_request(request: Mapping[str, Any]) -> dict:
    behavior = request["behavior"]
    if not isinstance(behavior, dict) or set(behavior) != {"cutoff_hz"}:
        raise ValueError("sallen_key_butterworth_lowpass behavior must contain only cutoff_hz")
    cutoff = behavior["cutoff_hz"]
    if not isinstance(cutoff, dict) or set(cutoff) - {"min", "max", "target"} or not {"min", "max"} <= set(cutoff):
        raise ValueError("cutoff_hz requires min/max and optionally target")
    low = _positive(cutoff["min"], "cutoff_hz.min")
    high = _positive(cutoff["max"], "cutoff_hz.max")
    if low > high:
        raise ValueError("cutoff_hz.min must be <= max")
    if high > _SALLEN_KEY_MAX_CUTOFF_HZ:
        raise ValueError(
            "sallen_key_butterworth_lowpass cutoff_hz.max must be <= 150000 Hz; "
            "the CP_OPAMP unity follower has about 15.9 MHz gain-bandwidth, and this demo "
            "keeps the requested corner below 1% of that model bandwidth"
        )
    target = _positive(cutoff["target"], "cutoff_hz.target") if "target" in cutoff else None
    if target is not None and not low <= target <= high:
        raise ValueError("cutoff_hz.target must be within min/max")
    names_limits = {
        "resistor_1_ohm": (1e-6, 1e12), "resistor_2_ohm": (1e-6, 1e12),
        "capacitor_1_f": (1e-15, 1e3), "capacitor_2_f": (1e-15, 1e3),
    }
    components = request["components"]
    if not isinstance(components, dict) or set(components) != set(names_limits):
        raise ValueError("components must contain resistor_1_ohm, resistor_2_ohm, capacitor_1_f, and capacitor_2_f")
    normalized_components = {}
    for name, (physical_min, physical_max) in names_limits.items():
        bounds = components[name]
        if not isinstance(bounds, dict) or set(bounds) != {"min", "max", "series"}:
            raise ValueError(f"components.{name} requires min, max, and series")
        component_low = _positive(bounds["min"], f"components.{name}.min")
        component_high = _positive(bounds["max"], f"components.{name}.max")
        if component_low > component_high or component_low < physical_min or component_high > physical_max:
            raise ValueError(f"components.{name} bounds are reversed or outside supported physical limits")
        series = bounds["series"]
        if not isinstance(series, str) or series.upper() not in _VALUES:
            raise ValueError(f"components.{name}.series must be E3, E6, E12, or E24")
        normalized_components[name] = {"min": component_low, "max": component_high, "series": series.upper()}
    normalized_cutoff = {"min": low, "max": high}
    if target is not None:
        normalized_cutoff["target"] = target
    return {"schema_version": 1, "topology": _SALLEN_KEY_TOPOLOGY,
            "behavior": {"cutoff_hz": normalized_cutoff}, "components": normalized_components}


def _synthesize_sallen_key(spec: Mapping[str, Any]) -> TopologyRequestResult:
    bounds = spec["behavior"]["cutoff_hz"]
    log_target = (math.log(bounds["target"]) if "target" in bounds
                  else (math.log(bounds["min"]) + math.log(bounds["max"])) / 2.0)
    values = {name: _standard_values(item) for name, item in spec["components"].items()}
    total = math.prod(len(items) for items in values.values())
    if total > _SALLEN_KEY_MAX_CANDIDATES:
        raise ValueError(f"component bounds create {total} E-series tuples; maximum supported search is {_SALLEN_KEY_MAX_CANDIDATES}")
    best = None
    best_key = None
    feasible_count = 0
    q_target = 1.0 / math.sqrt(2.0)
    for r1 in values["resistor_1_ohm"]:
        for r2 in values["resistor_2_ohm"]:
            for c1 in values["capacitor_1_f"]:
                for c2 in values["capacitor_2_f"]:
                    natural_hz = 1.0 / (2.0 * math.pi * math.sqrt(r1 * r2 * c1 * c2))
                    q_value = math.sqrt(r1 * r2 * c1 / c2) / (r1 + r2)
                    if not math.isfinite(natural_hz) or not math.isfinite(q_value):
                        continue
                    cutoff_hz = _sallen_key_cutoff_hz(natural_hz, q_value)
                    q_error = abs(q_value - q_target) / q_target
                    if not bounds["min"] <= cutoff_hz <= bounds["max"] or q_error > _SALLEN_KEY_Q_RELATIVE_TOLERANCE:
                        continue
                    feasible_count += 1
                    item = {"resistor_1_ohm": r1, "resistor_2_ohm": r2,
                            "capacitor_1_f": c1, "capacitor_2_f": c2,
                            "natural_frequency_hz": natural_hz, "cutoff_hz": cutoff_hz,
                            "q": q_value, "butterworth_q": q_target,
                            "q_relative_error_percent": 100.0 * q_error}
                    rank = (abs(math.log(cutoff_hz) - log_target), q_error, r1, r2, c1, c2)
                    if best_key is None or rank < best_key:
                        best, best_key = item, rank
    report = {
        "schema_version": 1, "topology": _SALLEN_KEY_TOPOLOGY,
        "status": "feasible" if best is not None else "infeasible",
        "constraint_evidence": "analytical", "verification_status": "not_simulated",
        "production_qualified": False,
        "hard_constraints": [
            {"name": "cutoff_hz", "min": bounds["min"], "max": bounds["max"],
             "pass": best is not None, "evidence": "analytical"},
            {"name": "butterworth_q_relative_error", "max_percent": 100 * _SALLEN_KEY_Q_RELATIVE_TOLERANCE,
             "pass": best is not None, "evidence": "analytical"},
        ],
        "candidate_search": {"component_tuples_considered": total,
                             "feasible_candidate_count": feasible_count,
                             "ranking_reference_hz": math.exp(log_target),
                             "ranking_method": "closest in log -3 dB cutoff to target or geometric range center, then Q error"},
        "candidate": best,
        "notes": ["Q is constrained to within 5% of the Butterworth value; this is an analytical synthesis screen.",
                  "The finite-bandwidth CP_OPAMP model is guarded by a 150 kHz maximum requested corner."],
    }
    if best is None:
        raise NoFeasibleCandidateError(report)
    elements = (
        CircuitElement.from_statement("VIN in 0 DC 0 AC 1"),
        CircuitElement.from_statement(f"R1 in n1 {format_spice_number(best['resistor_1_ohm'])}"),
        CircuitElement.from_statement(f"R2 n1 n2 {format_spice_number(best['resistor_2_ohm'])}"),
        CircuitElement.from_statement(f"C1 n1 out {format_spice_number(best['capacitor_1_f'])}"),
        CircuitElement.from_statement(f"C2 n2 0 {format_spice_number(best['capacitor_2_f'])}"),
        CircuitElement.from_statement("XU1 n2 out out CP_OPAMP"),
    )
    fc = best["cutoff_hz"]
    circuit = CircuitIR("CircuitPilot unity-gain Sallen-Key Butterworth low-pass", elements,
                        subcircuits=(_OPAMP.strip(),),
                        directives=(f".ac dec 100 {format_spice_number(fc / 100.0)} {format_spice_number(fc * 20.0)}",))
    return TopologyRequestResult(circuit, report)


def _sallen_key_cutoff_hz(natural_frequency_hz: float, q_factor: float) -> float:
    """Return the descending -3 dB frequency for a unity-gain second-order low-pass."""
    coefficient = (1.0 / (q_factor * q_factor)) - 2.0
    squared_ratio = (-coefficient + math.sqrt(coefficient * coefficient + 4.0)) / 2.0
    return natural_frequency_hz * math.sqrt(squared_ratio)


def _validate_inverting_opamp_request(request: Mapping[str, Any]) -> dict:
    behavior = request["behavior"]
    if not isinstance(behavior, dict) or set(behavior) != {"gain_magnitude"}:
        raise ValueError("inverting_opamp_amplifier behavior must contain only gain_magnitude")
    gain = behavior["gain_magnitude"]
    if not isinstance(gain, dict) or set(gain) - {"min", "max", "target"} or not {"min", "max"} <= set(gain):
        raise ValueError("gain_magnitude requires min/max and optionally target")
    low = _positive(gain["min"], "gain_magnitude.min")
    high = _positive(gain["max"], "gain_magnitude.max")
    if low > high:
        raise ValueError("gain_magnitude.min must be <= max")
    if high > _INVERTING_OPAMP_MAX_GAIN:
        raise ValueError(
            "gain_magnitude.max must be <= 100 V/V; CP_OPAMP has finite ~15.9 MHz gain-bandwidth "
            "and this demo caps closed-loop gain for model-safe behavior"
        )
    target = _positive(gain["target"], "gain_magnitude.target") if "target" in gain else None
    if target is not None and not low <= target <= high:
        raise ValueError("gain_magnitude.target must be within min/max")
    components = request["components"]
    names = {"input_resistance_ohm", "feedback_resistance_ohm"}
    if not isinstance(components, dict) or set(components) != names:
        raise ValueError("components must contain input_resistance_ohm and feedback_resistance_ohm")
    normalized_components = {}
    for name in sorted(names):
        bounds = components[name]
        if not isinstance(bounds, dict) or set(bounds) != {"min", "max", "series"}:
            raise ValueError(f"components.{name} requires min, max, and series")
        component_low = _positive(bounds["min"], f"components.{name}.min")
        component_high = _positive(bounds["max"], f"components.{name}.max")
        if component_low > component_high or component_low < 1e-6 or component_high > 1e12:
            raise ValueError(f"components.{name} bounds are reversed or outside supported physical limits")
        series = bounds["series"]
        if not isinstance(series, str) or series.upper() not in _VALUES:
            raise ValueError(f"components.{name}.series must be E3, E6, E12, or E24")
        normalized_components[name] = {"min": component_low, "max": component_high, "series": series.upper()}
    normalized_gain = {"min": low, "max": high}
    if target is not None:
        normalized_gain["target"] = target
    return {"schema_version": 1, "topology": _INVERTING_OPAMP_TOPOLOGY,
            "behavior": {"gain_magnitude": normalized_gain}, "components": normalized_components}


def _synthesize_inverting_opamp(spec: Mapping[str, Any]) -> TopologyRequestResult:
    bounds = spec["behavior"]["gain_magnitude"]
    log_target = (math.log(bounds["target"]) if "target" in bounds
                  else (math.log(bounds["min"]) + math.log(bounds["max"])) / 2.0)
    values = {name: _standard_values(item) for name, item in spec["components"].items()}
    input_values = values["input_resistance_ohm"]
    feedback_values = values["feedback_resistance_ohm"]
    candidates = []
    for input_resistance in input_values:
        for feedback_resistance in feedback_values:
            gain_magnitude = feedback_resistance / input_resistance
            if bounds["min"] <= gain_magnitude <= bounds["max"]:
                candidates.append({"input_resistance_ohm": input_resistance,
                                   "feedback_resistance_ohm": feedback_resistance,
                                   "gain_magnitude": gain_magnitude})
    candidates.sort(key=lambda item: (abs(math.log(item["gain_magnitude"]) - log_target),
                                      item["input_resistance_ohm"], item["feedback_resistance_ohm"]))
    target = math.exp(log_target)
    feasible = bool(candidates)
    report = {
        "schema_version": 1, "topology": _INVERTING_OPAMP_TOPOLOGY,
        "status": "feasible" if feasible else "infeasible",
        "constraint_evidence": "analytical", "verification_status": "not_simulated",
        "production_qualified": False,
        "hard_constraints": [{"name": "gain_magnitude", "min": bounds["min"], "max": bounds["max"],
                              "pass": feasible, "evidence": "analytical"}],
        "candidate_search": {"component_pairs_considered": len(input_values) * len(feedback_values),
                             "feasible_candidate_count": len(candidates),
                             "ranking_reference": target,
                             "ranking_method": "closest in log gain to target or geometric range center"},
        "candidate": candidates[0] if feasible else None,
    }
    if not feasible:
        raise NoFeasibleCandidateError(report)
    candidate = candidates[0]
    elements = (
        CircuitElement.from_statement("VIN in 0 DC 0 AC 1"),
        CircuitElement.from_statement(f"RIN in summing {format_spice_number(candidate['input_resistance_ohm'])}"),
        CircuitElement.from_statement(f"RF out summing {format_spice_number(candidate['feedback_resistance_ohm'])}"),
        CircuitElement.from_statement("XU1 0 summing out CP_OPAMP"),
    )
    circuit = CircuitIR("CircuitPilot inverting op-amp amplifier", elements, subcircuits=(_OPAMP.strip(),),
                        directives=(".ac dec 100 10 100Meg",))
    return TopologyRequestResult(circuit, report)


def _validate_series_rlc_request(request: Mapping[str, Any]) -> dict:
    if set(request) != {"schema_version", "topology", "behavior", "components"}:
        raise ValueError("series_rlc_bandpass accepts only schema_version, topology, behavior, and components")
    behavior = request["behavior"]
    if not isinstance(behavior, dict) or set(behavior) != {"resonance_hz"}:
        raise ValueError("series_rlc_bandpass behavior must contain only resonance_hz")
    resonance = behavior["resonance_hz"]
    if (not isinstance(resonance, dict) or set(resonance) - {"min", "max", "target"}
            or not {"min", "max"} <= set(resonance)):
        raise ValueError("resonance_hz requires min/max and optionally target")
    low = _positive(resonance["min"], "resonance_hz.min")
    high = _positive(resonance["max"], "resonance_hz.max")
    if low > high:
        raise ValueError("resonance_hz.min must be <= max")
    target = _positive(resonance["target"], "resonance_hz.target") if "target" in resonance else None
    if target is not None and not low <= target <= high:
        raise ValueError("resonance_hz.target must be within min/max")
    components = request["components"]
    component_names = {"resistance_ohm", "capacitance_f", "inductance_h"}
    if not isinstance(components, dict) or set(components) != component_names:
        raise ValueError("series_rlc_bandpass requires resistance_ohm, capacitance_f, and inductance_h bounds")
    limits = {"resistance_ohm": (1e-6, 1e12), "capacitance_f": (1e-15, 1e3),
              "inductance_h": (1e-12, 1e9)}
    normalized = {}
    for name, (physical_min, physical_max) in limits.items():
        bounds = components[name]
        if not isinstance(bounds, dict) or set(bounds) != {"min", "max", "series"}:
            raise ValueError(f"components.{name} requires min, max, and series")
        value_min = _positive(bounds["min"], f"components.{name}.min")
        value_max = _positive(bounds["max"], f"components.{name}.max")
        if value_min > value_max or value_min < physical_min or value_max > physical_max:
            raise ValueError(f"components.{name} bounds are reversed or outside supported physical limits")
        series = bounds["series"]
        if not isinstance(series, str) or series.upper() not in _VALUES:
            raise ValueError(f"components.{name}.series must be E3, E6, E12, or E24")
        normalized[name] = {"min": value_min, "max": value_max, "series": series.upper()}
    normalized_resonance = {"min": low, "max": high}
    if target is not None:
        normalized_resonance["target"] = target
    return {"schema_version": 1, "topology": _RLC_TOPOLOGY,
            "behavior": {"resonance_hz": normalized_resonance}, "components": normalized}


def _synthesize_series_rlc(spec: Mapping[str, Any]) -> TopologyRequestResult:
    bounds = spec["behavior"]["resonance_hz"]
    log_target = (math.log(bounds["target"]) if "target" in bounds
                  else (math.log(bounds["min"]) + math.log(bounds["max"])) / 2.0)
    values = {name: _standard_values(item) for name, item in spec["components"].items()}
    resistance_values = values["resistance_ohm"]
    capacitances = values["capacitance_f"]
    inductances = values["inductance_h"]
    # Resonance is independent of R. The prior tuple sort ranked identical C/L
    # resonances by ascending R, so retaining only the first E-series R preserves
    # the selected candidate while avoiding an R×C×L materialized search.
    resistance = resistance_values[0] if resistance_values else None
    best_candidate = None
    best_rank = None
    feasible_pair_count = 0
    pairs_considered = 0
    if resistance is not None:
        for capacitance in capacitances:
            for inductance in inductances:
                pairs_considered += 1
                resonance = 1.0 / (2.0 * math.pi * math.sqrt(inductance * capacitance))
                if not bounds["min"] <= resonance <= bounds["max"]:
                    continue
                feasible_pair_count += 1
                rank = (abs(math.log(resonance) - log_target), resistance, capacitance, inductance)
                if best_rank is None or rank < best_rank:
                    best_rank = rank
                    best_candidate = {"resistance_ohm": resistance, "capacitance_f": capacitance,
                                      "inductance_h": inductance, "resonance_hz": resonance}
    target = math.exp(log_target)
    report = {
        "schema_version": 1, "topology": _RLC_TOPOLOGY,
        "status": "feasible" if best_candidate is not None else "infeasible",
        "constraint_evidence": "analytical", "verification_status": "not_simulated",
        "production_qualified": False,
        "hard_constraints": [{"name": "resonance_hz", "min": bounds["min"], "max": bounds["max"],
                              "pass": best_candidate is not None, "evidence": "analytical"}],
        "candidate_search": {"component_pairs_considered": pairs_considered,
                             "candidate_tuples_considered": pairs_considered * len(resistance_values),
                             "available_component_values": {
                                 "resistance_ohm": len(resistance_values),
                                 "capacitance_f": len(capacitances),
                                 "inductance_h": len(inductances),
                             },
                             "feasible_resonance_pair_count": feasible_pair_count,
                             "feasible_candidate_count": feasible_pair_count * len(resistance_values),
                             "ranking_reference_hz": target,
                             "ranking_method": "closest in log frequency to target or geometric range center",
                             "resistance_selection": "lowest available E-series value; resonance is independent of resistance"},
        "candidate": best_candidate,
    }
    if best_candidate is None:
        raise NoFeasibleCandidateError(report)
    selected = best_candidate
    elements = (
        CircuitElement.from_statement("V1 in 0 AC 1"),
        CircuitElement.from_statement(f"C1 in mid {format_spice_number(selected['capacitance_f'])}"),
        CircuitElement.from_statement(f"L1 mid out {format_spice_number(selected['inductance_h'])}"),
        CircuitElement.from_statement(f"R1 out 0 {format_spice_number(selected['resistance_ohm'])}"),
    )
    circuit = CircuitIR("CircuitPilot experimental series RLC band-pass", elements,
                        directives=(".ac dec 100 1 1Meg",))
    return TopologyRequestResult(circuit, report)


def _positive(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{label} must be finite and positive")
    return float(value)
