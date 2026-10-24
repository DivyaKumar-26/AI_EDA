"""Strict behavioral requests and deterministic initial synthesis for RC low-pass filters."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Mapping

from .ir import CircuitElement, CircuitIR
from .rc_lowpass import build_rc_lowpass, cutoff_frequency_hz, format_spice_number


_SERIES_VALUES = {
    "E3": (1.0, 2.2, 4.7),
    "E6": (1.0, 1.5, 2.2, 3.3, 4.7, 6.8),
    "E12": (1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2),
    "E24": (1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0,
           3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1),
}
_COMPONENT_LIMITS = {
    "resistance_ohm": (1e-6, 1e12),
    "capacitance_f": (1e-15, 1e3),
}


class NoFeasibleCandidateError(ValueError):
    """Raised when no allowed standard-value pair satisfies the hard bounds."""

    def __init__(self, report: dict):
        self.report = report
        super().__init__("no standard E-series RC candidate satisfies the request's hard bounds")


@dataclass(frozen=True)
class RCSynthesisResult:
    circuit_ir: CircuitIR
    report: dict


def validate_rc_request(request: Any) -> dict:
    """Validate and normalize the versioned behavioral request; unknown fields fail closed."""
    _require_object(request, "request")
    _require_keys(request, {"schema_version", "topology", "behavior", "components"}, "request",
                   optional={"operating_conditions"})
    if request["schema_version"] != 1 or isinstance(request["schema_version"], bool):
        raise ValueError("schema_version must be 1")
    if request["topology"] != "rc_lowpass":
        raise ValueError("topology must be 'rc_lowpass' for this synthesis entry point")

    behavior = request["behavior"]
    _require_object(behavior, "behavior")
    _require_keys(behavior, {"cutoff_hz"}, "behavior", optional={"passband_gain"})
    cutoff = behavior["cutoff_hz"]
    _require_object(cutoff, "behavior.cutoff_hz")
    _require_keys(cutoff, {"min", "max"}, "behavior.cutoff_hz", optional={"target"})
    cutoff_min = _finite_positive(cutoff["min"], "behavior.cutoff_hz.min")
    cutoff_max = _finite_positive(cutoff["max"], "behavior.cutoff_hz.max")
    if cutoff_min > cutoff_max:
        raise ValueError("behavior.cutoff_hz.min must be less than or equal to max")
    if "target" in cutoff and cutoff["target"] is None:
        raise ValueError("behavior.cutoff_hz.target must be omitted or a finite positive number")
    target = None if "target" not in cutoff else _finite_positive(cutoff["target"], "behavior.cutoff_hz.target")
    if target is not None and not cutoff_min <= target <= cutoff_max:
        raise ValueError("behavior.cutoff_hz.target must lie within its hard min/max range")

    normalized_cutoff = {"min": cutoff_min, "max": cutoff_max}
    if target is not None:
        normalized_cutoff["target"] = target
    normalized_behavior = {"cutoff_hz": normalized_cutoff}
    if "passband_gain" in behavior:
        gain = behavior["passband_gain"]
        _require_object(gain, "behavior.passband_gain")
        _require_keys(gain, {"min", "max"}, "behavior.passband_gain")
        gain_min = _finite_positive(gain["min"], "behavior.passband_gain.min")
        gain_max = _finite_positive(gain["max"], "behavior.passband_gain.max")
        if gain_min > gain_max or gain_max > 1.0:
            raise ValueError("behavior.passband_gain must satisfy 0 < min <= max <= 1")
        normalized_behavior["passband_gain"] = {"min": gain_min, "max": gain_max}

    operating_conditions = None
    if "operating_conditions" in request:
        conditions = request["operating_conditions"]
        _require_object(conditions, "operating_conditions")
        _require_keys(conditions, {"source_resistance_ohm", "load_resistance_ohm"}, "operating_conditions")
        source_resistance = _finite_nonnegative(conditions["source_resistance_ohm"],
                                               "operating_conditions.source_resistance_ohm")
        load_raw = conditions["load_resistance_ohm"]
        load_resistance = None if load_raw is None else _finite_positive(
            load_raw, "operating_conditions.load_resistance_ohm")
        operating_conditions = {
            "source_resistance_ohm": source_resistance,
            "load_resistance_ohm": load_resistance,
        }

    components = request["components"]
    _require_object(components, "components")
    _require_keys(components, set(_COMPONENT_LIMITS), "components")
    normalized_components = {}
    for component_name, (physical_min, physical_max) in _COMPONENT_LIMITS.items():
        bounds = components[component_name]
        _require_object(bounds, f"components.{component_name}")
        _require_keys(bounds, {"min", "max", "series"}, f"components.{component_name}")
        low = _finite_positive(bounds["min"], f"components.{component_name}.min")
        high = _finite_positive(bounds["max"], f"components.{component_name}.max")
        if low > high:
            raise ValueError(f"components.{component_name}.min must be less than or equal to max")
        if low < physical_min or high > physical_max:
            raise ValueError(
                f"components.{component_name} bounds must be within [{physical_min:g}, {physical_max:g}]"
            )
        series = bounds["series"]
        if not isinstance(series, str) or series.upper() not in _SERIES_VALUES:
            raise ValueError(f"components.{component_name}.series must be one of {', '.join(_SERIES_VALUES)}")
        normalized_components[component_name] = {"min": low, "max": high, "series": series.upper()}

    return {
        "schema_version": 1,
        "topology": "rc_lowpass",
        "behavior": normalized_behavior,
        "components": normalized_components,
        **({"operating_conditions": operating_conditions} if operating_conditions is not None else {}),
    }


def load_rc_request(path: str | Path) -> dict:
    """Load and validate a UTF-8 JSON behavioral request."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read valid JSON request: {exc}") from exc
    return validate_rc_request(raw)


def synthesize_rc_request(request: Mapping[str, Any]) -> RCSynthesisResult:
    """Select an E-series RC pair satisfying hard constraints and return its CircuitIR/report."""
    spec = validate_rc_request(dict(request))
    cutoff_bounds = spec["behavior"]["cutoff_hz"]
    r_values = _standard_values(spec["components"]["resistance_ohm"])
    c_values = _standard_values(spec["components"]["capacitance_f"])
    candidates = []
    for resistance in r_values:
        for capacitance in c_values:
            try:
                cutoff_hz = _candidate_cutoff_hz(resistance, capacitance, spec)
            except ValueError:
                continue
            passband_gain = _candidate_passband_gain(resistance, spec)
            candidates.append({
                "resistance_ohm": resistance,
                "capacitance_f": capacitance,
                "cutoff_hz": cutoff_hz,
                "passband_gain": passband_gain,
            })

    candidates = [candidate for candidate in candidates if _candidate_feasible(candidate, spec)]

    component_pairs_considered = len(r_values) * len(c_values)
    ranking_target = cutoff_bounds.get("target")
    if ranking_target is None:
        ranking_target = math.exp((math.log(cutoff_bounds["min"]) + math.log(cutoff_bounds["max"])) / 2.0)
        ranking_method = "closest in log frequency to geometric center of hard range"
    else:
        ranking_method = "closest in log frequency to requested target"
    if "passband_gain" in spec["behavior"]:
        ranking_method += "; candidates must also satisfy the requested passband-gain range"
    candidates.sort(key=lambda item: _candidate_sort_key(item, spec, ranking_target))

    base_report = {
        "schema_version": 1,
        "topology": "rc_lowpass",
        "status": "infeasible" if not candidates else "feasible",
        "constraint_evidence": "analytical",
        "verification_status": "not_simulated",
        "production_qualified": False,
        "applied_operating_conditions": _effective_conditions(spec),
        "hard_constraints": [
            {"name": "cutoff_hz", "min": cutoff_bounds["min"], "max": cutoff_bounds["max"],
             "target": cutoff_bounds.get("target"),
             "pass": any(cutoff_bounds["min"] <= c["cutoff_hz"] <= cutoff_bounds["max"]
                         for c in _all_candidate_values(spec)), "evidence": "analytical"},
            {"name": "resistance_ohm", **spec["components"]["resistance_ohm"],
             "pass": bool(r_values), "evidence": "analytical"},
            {"name": "capacitance_f", **spec["components"]["capacitance_f"],
             "pass": bool(c_values), "evidence": "analytical"},
        ],
        "candidate_search": {
            "resistance_values_considered": len(r_values),
            "capacitance_values_considered": len(c_values),
            "component_pairs_considered": component_pairs_considered,
            "feasible_candidate_count": len(candidates),
            "ranking_method": ranking_method,
            "ranking_reference_hz": ranking_target,
        },
        "candidate": None,
    }
    if "passband_gain" in spec["behavior"]:
        gain_bounds = spec["behavior"]["passband_gain"]
        base_report["hard_constraints"].append({
            "name": "passband_gain", **gain_bounds,
            "pass": any(gain_bounds["min"] <= c["passband_gain"] <= gain_bounds["max"]
                        for c in _all_candidate_values(spec)), "evidence": "analytical",
        })
    if not candidates:
        raise NoFeasibleCandidateError(base_report)

    selected = candidates[0]
    log_error = (
        math.log(selected["cutoff_hz"]) - math.log(cutoff_bounds["target"])
        if cutoff_bounds.get("target") is not None else None
    )
    percent_error = None
    if log_error is not None:
        try:
            percent_error = 100.0 * math.expm1(log_error)
        except OverflowError:
            percent_error = None
        if percent_error is not None and not math.isfinite(percent_error):
            percent_error = None
    candidate_report = {
        **selected,
        "cutoff_log_error_vs_target": log_error,
        "cutoff_error_percent_vs_target": percent_error,
    }
    base_report["candidate"] = candidate_report
    circuit_ir = _candidate_circuit_ir(selected["resistance_ohm"], selected["capacitance_f"], spec)
    return RCSynthesisResult(circuit_ir=circuit_ir, report=base_report)


def rc_request_candidate_count(request: Mapping[str, Any]) -> int:
    """Return the number of bounded E-series R/C pairs in deterministic refinement order."""
    spec = validate_rc_request(dict(request))
    return len(_ranked_candidate_values(spec))


def synthesize_rc_request_candidate(request: Mapping[str, Any], candidate_index: int) -> RCSynthesisResult:
    """Synthesize a ranked pair selected by index, deriving both spec and pair internally."""
    if isinstance(candidate_index, bool) or not isinstance(candidate_index, int) or candidate_index < 0:
        raise ValueError("candidate_index must be a nonnegative integer")
    spec = validate_rc_request(dict(request))
    ranked = _ranked_candidate_values(spec)
    if candidate_index >= len(ranked):
        raise IndexError(f"candidate_index {candidate_index} is outside the {len(ranked)}-candidate search space")
    selected = ranked[candidate_index]
    cutoff_bounds = spec["behavior"]["cutoff_hz"]
    cutoff_pass = cutoff_bounds["min"] <= selected["cutoff_hz"] <= cutoff_bounds["max"]
    analytical_pass = _candidate_feasible(selected, spec)
    target = cutoff_bounds.get("target")
    if target is None:
        target = math.exp((math.log(cutoff_bounds["min"]) + math.log(cutoff_bounds["max"])) / 2.0)
        ranking_method = "closest in log frequency to geometric center of hard range"
    else:
        ranking_method = "closest in log frequency to requested target"
    if "passband_gain" in spec["behavior"]:
        ranking_method += "; among cutoff-ranked candidates, requested passband-gain feasibility is preferred"
    log_error = math.log(selected["cutoff_hz"]) - math.log(cutoff_bounds["target"]) if cutoff_bounds.get("target") else None
    percent_error = None
    if log_error is not None:
        try:
            percent_error = 100.0 * math.expm1(log_error)
        except OverflowError:
            pass
        if percent_error is not None and not math.isfinite(percent_error):
            percent_error = None
    candidate = {
        **selected,
        "cutoff_log_error_vs_target": log_error,
        "cutoff_error_percent_vs_target": percent_error,
    }
    r_values = _standard_values(spec["components"]["resistance_ohm"])
    c_values = _standard_values(spec["components"]["capacitance_f"])
    report = {
        "schema_version": 1,
        "topology": "rc_lowpass",
        "status": "feasible" if analytical_pass else "infeasible",
        "constraint_evidence": "analytical",
        "verification_status": "not_simulated",
        "production_qualified": False,
        "applied_operating_conditions": _effective_conditions(spec),
        "hard_constraints": [
            {"name": "cutoff_hz", "min": cutoff_bounds["min"], "max": cutoff_bounds["max"],
             "target": cutoff_bounds.get("target"), "pass": cutoff_pass, "evidence": "analytical"},
            {"name": "resistance_ohm", **spec["components"]["resistance_ohm"], "pass": True,
             "evidence": "analytical"},
            {"name": "capacitance_f", **spec["components"]["capacitance_f"], "pass": True,
             "evidence": "analytical"},
        ],
        "candidate_search": {
            "resistance_values_considered": len(r_values),
            "capacitance_values_considered": len(c_values),
            "component_pairs_considered": len(r_values) * len(c_values),
            "feasible_candidate_count": sum(_candidate_feasible(value, spec) for value in ranked),
            "ranked_candidate_count": len(ranked),
            "candidate_index": candidate_index,
            "ranking_method": ranking_method,
            "ranking_reference_hz": target,
        },
        "candidate": candidate,
    }
    if "passband_gain" in spec["behavior"]:
        gain_bounds = spec["behavior"]["passband_gain"]
        report["hard_constraints"].append({
            "name": "passband_gain", **gain_bounds,
            "pass": gain_bounds["min"] <= selected["passband_gain"] <= gain_bounds["max"],
            "evidence": "analytical",
        })
    circuit_ir = _candidate_circuit_ir(selected["resistance_ohm"], selected["capacitance_f"], spec)
    return RCSynthesisResult(circuit_ir, report)


def _ranked_candidate_values(spec: Mapping[str, Any]) -> list[dict[str, float]]:
    candidates = _all_candidate_values(spec)
    cutoff_bounds = spec["behavior"]["cutoff_hz"]
    target = cutoff_bounds.get("target")
    if target is None:
        target = math.exp((math.log(cutoff_bounds["min"]) + math.log(cutoff_bounds["max"])) / 2.0)
    candidates.sort(key=lambda item: _candidate_sort_key(item, spec, target))
    return candidates


def _all_candidate_values(spec: Mapping[str, Any]) -> list[dict[str, float]]:
    cutoff_bounds = spec["behavior"]["cutoff_hz"]
    r_values = _standard_values(spec["components"]["resistance_ohm"])
    c_values = _standard_values(spec["components"]["capacitance_f"])
    candidates = []
    for resistance in r_values:
        for capacitance in c_values:
            try:
                cutoff_hz = _candidate_cutoff_hz(resistance, capacitance, spec)
            except ValueError:
                continue
            candidates.append({
                "resistance_ohm": resistance,
                "capacitance_f": capacitance,
                "cutoff_hz": cutoff_hz,
                "passband_gain": _candidate_passband_gain(resistance, spec),
            })
    return candidates


def _effective_conditions(spec: Mapping[str, Any]) -> dict[str, float | None]:
    return spec.get("operating_conditions", {
        "source_resistance_ohm": 0.0,
        "load_resistance_ohm": None,
    })


def _candidate_cutoff_hz(resistance: float, capacitance: float, spec: Mapping[str, Any]) -> float:
    conditions = _effective_conditions(spec)
    series_total = conditions["source_resistance_ohm"] + resistance
    if not math.isfinite(series_total):
        if conditions["load_resistance_ohm"] is None:
            raise ValueError("effective RC resistance is outside the representable range")
        rth = conditions["load_resistance_ohm"]
    elif conditions["load_resistance_ohm"] is None:
        return cutoff_frequency_hz(series_total, capacitance)
    else:
        load = conditions["load_resistance_ohm"]
        low, high = min(series_total, load), max(series_total, load)
        rth = low / (1.0 + low / high)
    return cutoff_frequency_hz(rth, capacitance)


def _candidate_passband_gain(resistance: float, spec: Mapping[str, Any]) -> float:
    conditions = _effective_conditions(spec)
    load = conditions["load_resistance_ohm"]
    if load is None:
        return 1.0
    source = conditions["source_resistance_ohm"]
    scale = max(source, resistance, load)
    return (load / scale) / (source / scale + resistance / scale + load / scale)


def _candidate_feasible(candidate: Mapping[str, float], spec: Mapping[str, Any]) -> bool:
    cutoff = spec["behavior"]["cutoff_hz"]
    if not cutoff["min"] <= candidate["cutoff_hz"] <= cutoff["max"]:
        return False
    gain = spec["behavior"].get("passband_gain")
    return gain is None or gain["min"] <= candidate["passband_gain"] <= gain["max"]


def _candidate_sort_key(candidate: Mapping[str, float], spec: Mapping[str, Any], target: float) -> tuple:
    cutoff = spec["behavior"]["cutoff_hz"]
    gain = spec["behavior"].get("passband_gain")
    gain_distance = 0.0
    if gain is not None:
        gain_target = math.exp((math.log(gain["min"]) + math.log(gain["max"])) / 2.0)
        gain_distance = (
            abs(math.log(candidate["passband_gain"]) - math.log(gain_target))
            if candidate["passband_gain"] > 0 else math.inf
        )
    return (
        (0 if gain is None or gain["min"] <= candidate["passband_gain"] <= gain["max"] else 1),
        abs(math.log(candidate["cutoff_hz"]) - math.log(target)),
        gain_distance,
        candidate["resistance_ohm"],
        candidate["capacitance_f"],
    )


def _candidate_circuit_ir(resistance: float, capacitance: float, spec: Mapping[str, Any]) -> CircuitIR:
    conditions = spec.get("operating_conditions")
    if conditions is None:
        circuit = build_rc_lowpass(resistance, capacitance)
        return CircuitIR.from_spice_text(circuit.to_spice(), title="CircuitPilot first-order RC low-pass")
    source_resistance = conditions["source_resistance_ohm"]
    load_resistance = conditions["load_resistance_ohm"]
    source_node = "src"
    filter_input_node = "in" if source_resistance > 0 else source_node
    elements = [CircuitElement.from_statement(f"V1 {source_node} 0 AC 1")]
    if source_resistance > 0:
        elements.append(CircuitElement.from_statement(
            f"Rsource {source_node} {filter_input_node} {format_spice_number(source_resistance)}"))
    elements.append(CircuitElement.from_statement(
        f"R1 {filter_input_node} out {format_spice_number(resistance)}"))
    elements.append(CircuitElement.from_statement(f"C1 out 0 {format_spice_number(capacitance)}"))
    if load_resistance is not None:
        elements.append(CircuitElement.from_statement(f"Rload out 0 {format_spice_number(load_resistance)}"))
    return CircuitIR("CircuitPilot first-order RC low-pass", tuple(elements), directives=(".ac dec 100 1 1Meg",))


def _standard_values(bounds: dict) -> list[float]:
    low, high = bounds["min"], bounds["max"]
    normalized = _SERIES_VALUES[bounds["series"]]
    first_decade = math.floor(math.log10(low)) - 1
    last_decade = math.floor(math.log10(high)) + 1
    values = {
        base * (10.0 ** decade)
        for decade in range(first_decade, last_decade + 1)
        for base in normalized
        if low <= base * (10.0 ** decade) <= high
    }
    return sorted(values)


def _require_object(value: Any, label: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")


def _require_keys(value: dict, required: set[str], label: str, optional: set[str] | None = None) -> None:
    optional = optional or set()
    missing = required - value.keys()
    extra = value.keys() - (required | optional)
    if missing:
        raise ValueError(f"{label} missing field(s): {', '.join(sorted(missing))}")
    if extra:
        raise ValueError(f"{label} has unsupported field(s): {', '.join(sorted(extra))}")


def _finite_positive(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite positive number")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite positive number") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{label} must be a finite positive number")
    return number


def _finite_nonnegative(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite nonnegative number")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite nonnegative number") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{label} must be a finite nonnegative number")
    return number
