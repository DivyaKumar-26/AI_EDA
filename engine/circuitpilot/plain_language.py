"""Deterministic offline intake for documented behavioral request phrases."""

from __future__ import annotations

from decimal import Decimal, DecimalException
import re
from typing import Any

from .rc_request import validate_rc_request
from .topology_requests import validate_topology_request


_NUMBER = r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_UNIT = r"[a-zA-ZµμΩ/%]+"
_QUANTITY = rf"({_NUMBER})\s*({_UNIT})"
_RANGE = rf"{_QUANTITY}\s+(?:to|through|and)\s+{_QUANTITY}"
_SERIES = r"E(?:3|6|12|24)"

_UNIT_SCALE = {
    "frequency": {"hz": 1.0, "khz": 1e3, "mhz": 1e6},
    "resistance": {"ohm": 1.0, "ohms": 1.0, "ω": 1.0, "kohm": 1e3, "kω": 1e3},
    "capacitance": {"f": 1.0, "mf": 1e-3, "uf": 1e-6, "μf": 1e-6, "µf": 1e-6,
                    "nf": 1e-9, "pf": 1e-12},
    "inductance": {"h": 1.0, "mh": 1e-3, "uh": 1e-6, "μh": 1e-6, "µh": 1e-6,
                   "nh": 1e-9},
    "gain": {"v/v": 1.0},
}

_TOPOLOGY_PATTERNS = (
    ("inverting_opamp_amplifier", re.compile(r"inverting\s+op[- ]amp(?:lifier)?(?:\s+amplifier)?", re.I)),
    ("series_rlc_bandpass", re.compile(r"series\s+RLC\s+band[- ]pass(?:\s+filter)?", re.I)),
    ("opamp_rc_lowpass", re.compile(
        r"(?:op[- ]amp\s+buffered\s+RC|buffered\s+(?:op[- ]amp\s+)?RC)\s+low[- ]pass(?:\s+filter)?", re.I)),
    ("rc_highpass", re.compile(r"RC\s+high[- ]pass(?:\s+filter)?", re.I)),
    ("rl_lowpass", re.compile(r"RL\s+low[- ]pass(?:\s+filter)?", re.I)),
    ("rc_lowpass", re.compile(r"RC\s+low[- ]pass(?:\s+filter)?", re.I)),
)

_TOPOLOGY_REQUIREMENTS = {
    "rc_lowpass": ("cutoff_hz", {"resistance_ohm": ("resistance", "resistance"),
                                    "capacitance_f": ("capacitance", "capacitance")}),
    "rc_highpass": ("cutoff_hz", {"resistance_ohm": ("resistance", "resistance"),
                                     "capacitance_f": ("capacitance", "capacitance")}),
    "rl_lowpass": ("cutoff_hz", {"resistance_ohm": ("resistance", "resistance"),
                                    "inductance_h": ("inductance", "inductance")}),
    "opamp_rc_lowpass": ("cutoff_hz", {"resistance_ohm": ("resistance", "resistance"),
                                         "capacitance_f": ("capacitance", "capacitance")}),
    "series_rlc_bandpass": ("resonance_hz", {"resistance_ohm": ("resistance", "resistance"),
                                               "capacitance_f": ("capacitance", "capacitance"),
                                               "inductance_h": ("inductance", "inductance")}),
    "inverting_opamp_amplifier": ("gain_magnitude", {
        "input_resistance_ohm": ("input\\s+(?:resistance|resistor)", "resistance"),
        "feedback_resistance_ohm": ("feedback\\s+(?:resistance|resistor)", "resistance"),
    }),
}

_LABEL_FORMAT_HINT = (
    "RC low-pass; cutoff: 1.5 kHz to 1.7 kHz; target: 1.59155 kHz; "
    "resistance: 1 kOhm to 10 kOhm E12; capacitance: 50 nF to 150 nF E12"
)
_SENTENCE_FORMAT_HINT = (
    "Design an RC low-pass filter with cutoff from 1.5 kHz to 1.7 kHz, "
    "resistance from 1 kOhm to 10 kOhm using E12, and capacitance from 50 nF to 150 nF using E12."
)


def parse_plain_language_request(text: Any) -> dict:
    """Convert a supported prompt to a topology-validated structured request.

    The legacy semicolon-labeled RC low-pass syntax remains supported. Sentence
    input follows an explicit grammar with named topology, behavior range, and
    component ranges. This is not general natural-language understanding.
    """
    try:
        if not isinstance(text, str) or not text.strip():
            return _clarification(["topology"], "Provide a non-empty request.", _SENTENCE_FORMAT_HINT)
        if ";" in text:
            return _parse_labeled_rc_lowpass(text)
        return _parse_sentence(text)
    except (ValueError, DecimalException) as exc:
        return {
            "status": "invalid_request",
            "message": str(exc),
            "supported_format": _SENTENCE_FORMAT_HINT,
        }


def _parse_labeled_rc_lowpass(text: str) -> dict:
    """Parse the original semicolon-labeled RC low-pass syntax unchanged."""
    if re.search(r"\b(high[- ]pass|bandpass|amplifier|opamp|operating condition|source|load|gain|inductor|rlc)\b",
                 text, re.IGNORECASE):
        return _unsupported(text, "The semicolon-labeled syntax currently supports RC low-pass only.")

    clauses = [clause.strip().rstrip(".").strip() for clause in text.strip().split(";") if clause.strip()]
    values: dict[str, Any] = {}
    malformed: list[str] = []
    unknown: list[str] = []
    for index, original_clause in enumerate(clauses):
        clause = re.sub(r"^design\s+(?:an?\s+)?", "", original_clause, flags=re.IGNORECASE).strip()
        if index == 0 and re.fullmatch(r"(?:topology\s*:\s*)?rc\s+low[- ]pass", clause, re.IGNORECASE):
            values["topology"] = "rc_lowpass"
            continue
        match = re.fullmatch(rf"cutoff\s*:\s*{_RANGE}", clause, re.IGNORECASE)
        if match:
            if "cutoff" in values:
                malformed.append("cutoff")
            values["cutoff"] = _read_range(match, "frequency", "cutoff")
            continue
        match = re.fullmatch(rf"target\s*:\s*{_QUANTITY}", clause, re.IGNORECASE)
        if match:
            if "target" in values:
                malformed.append("target")
            values["target"] = _read_quantity(match.group(1), match.group(2), "frequency", "target")
            continue
        matched_component = False
        for label, dimension in (("resistance", "resistance"), ("capacitance", "capacitance")):
            match = re.fullmatch(
                rf"{label}\s*:\s*{_RANGE}\s*(?:,?\s*(?:e-series\s*)?({_SERIES}))?",
                clause, re.IGNORECASE,
            )
            if match:
                if label in values:
                    malformed.append(label)
                low = _read_quantity(match.group(1), match.group(2), dimension, label)
                high = _read_quantity(match.group(3), match.group(4), dimension, label)
                series = (match.group(5) or "").upper()
                values[label] = {"min": low, "max": high, "series": series}
                if not series:
                    malformed.append(label)
                matched_component = True
                break
        if matched_component:
            continue
        known_label = re.match(r"^(cutoff|target|resistance|capacitance)\s*:", clause, re.IGNORECASE)
        if known_label:
            malformed.append(known_label.group(1).lower())
        else:
            unknown.append(original_clause)

    if unknown:
        return {
            "status": "unsupported",
            "message": "Some prompt text is outside the supported phrase format; nothing was inferred from it.",
            "unsupported_phrasing": unknown,
            "supported_format": _LABEL_FORMAT_HINT,
        }
    missing = [key for key in ("topology", "cutoff", "resistance", "capacitance") if key not in values]
    if missing or malformed:
        fields = list(dict.fromkeys(missing + malformed))
        return _clarification(fields, "Complete or correct the labeled RC low-pass clauses.", _LABEL_FORMAT_HINT)

    behavior: dict[str, Any] = {"cutoff_hz": {"min": values["cutoff"]["min"], "max": values["cutoff"]["max"]}}
    if "target" in values:
        behavior["cutoff_hz"]["target"] = values["target"]
    request = {
        "schema_version": 1,
        "topology": "rc_lowpass",
        "behavior": behavior,
        "components": {
            "resistance_ohm": values["resistance"],
            "capacitance_f": values["capacitance"],
        },
    }
    return _complete(request)


def _parse_sentence(text: str) -> dict:
    source = re.sub(r"^\s*(?:please\s+)?(?:design\s+(?:an?\s+)?)?", "", text.strip(), flags=re.I)
    topology_matches = [(topology, pattern.match(source)) for topology, pattern in _TOPOLOGY_PATTERNS]
    topology_matches = [(topology, match) for topology, match in topology_matches if match]
    if len(topology_matches) != 1:
        return _unsupported(text, "Name exactly one supported topology at the start of the request.")
    topology, topology_match = topology_matches[0]
    residual = source[topology_match.end():]
    behavior_name, component_specs = _TOPOLOGY_REQUIREMENTS[topology]
    behavior_dimension = "gain" if behavior_name == "gain_magnitude" else "frequency"
    behavior_word = "gain\\s+magnitude" if behavior_name == "gain_magnitude" else (
        "resonance(?:\\s+frequency)?" if behavior_name == "resonance_hz" else "cutoff(?:\\s+frequency)?"
    )
    behavior_matches = list(re.finditer(
        rf"\b{behavior_word}\s+(?:from|between)\s+{_RANGE}", residual, re.I
    ))
    malformed: list[str] = []
    if len(behavior_matches) > 1:
        malformed.append(behavior_name)
    behavior: dict[str, float] = {}
    if behavior_matches:
        match = behavior_matches[0]
        behavior = _read_range(match, behavior_dimension, behavior_name)
        residual = _remove_matches(residual, behavior_matches)

    target_matches = list(re.finditer(rf"\btarget\s+{_QUANTITY}", residual, re.I))
    target = None
    if len(target_matches) > 1:
        malformed.append(f"{behavior_name}.target")
    if target_matches:
        match = target_matches[0]
        target = _read_quantity(match.group(1), match.group(2), behavior_dimension, f"{behavior_name}.target")
        residual = _remove_matches(residual, target_matches)

    components: dict[str, dict[str, Any]] = {}
    for request_key, (label_pattern, dimension) in component_specs.items():
        pattern = re.compile(
            rf"\b(?:{label_pattern})\s+(?:from|between)\s+{_RANGE}"
            rf"\s*(?:using\s+)?({_SERIES})?(?:\s+series)?\b",
            re.I,
        )
        matches = list(pattern.finditer(residual))
        if len(matches) > 1:
            malformed.append(request_key)
        if matches:
            match = matches[0]
            series = (match.group(5) or "").upper()
            components[request_key] = {
                "min": _read_quantity(match.group(1), match.group(2), dimension, request_key),
                "max": _read_quantity(match.group(3), match.group(4), dimension, request_key),
                "series": series,
            }
            if not series:
                malformed.append(request_key)
            residual = _remove_matches(residual, matches)

    residual_behavior_terms = re.findall(r"\b(cutoff|frequency|resonance|gain|magnitude)\b", residual, re.I)
    expected_behavior_terms = {
        "cutoff_hz": {"cutoff", "frequency"},
        "resonance_hz": {"resonance", "frequency"},
        "gain_magnitude": {"gain", "magnitude"},
    }[behavior_name]
    if any(term.lower() not in expected_behavior_terms for term in residual_behavior_terms) or (
        behavior and residual_behavior_terms
    ):
        return {
            "status": "unsupported",
            "message": "The sentence includes an additional or unsupported behavior requirement.",
            "unsupported_phrasing": residual_behavior_terms,
            "supported_format": _SENTENCE_FORMAT_HINT,
        }

    residual_words = re.sub(
        r"\b(?:please|design|a|an|the|filter|amplifier|with|and|using|from|between|to|is|has|having|"
        r"range|cutoff|frequency|resonance|gain|magnitude|target|resistance|resistor|input|feedback|"
        r"capacitance|capacitor|inductance|inductor|series|e3|e6|e12|e24)\b",
        " ", residual, flags=re.I,
    )
    residual_words = re.sub(r"[\s,.;:()/-]+", "", residual_words)
    if residual_words:
        return {
            "status": "unsupported",
            "message": "The sentence contains unsupported or ambiguous wording; no values were inferred from it.",
            "unsupported_phrasing": [residual_words],
            "supported_format": _SENTENCE_FORMAT_HINT,
        }

    missing = []
    if not behavior:
        missing.append(behavior_name)
    if target is not None and behavior:
        behavior["target"] = target
    for request_key in component_specs:
        if request_key not in components:
            missing.append(f"components.{request_key}")
    if missing or malformed:
        return _clarification(list(dict.fromkeys(missing + malformed)),
                              "Provide every requested behavior range and component bound with an E-series.",
                              _SENTENCE_FORMAT_HINT)

    request = {
        "schema_version": 1,
        "topology": topology,
        "behavior": {behavior_name: behavior},
        "components": components,
    }
    return _complete(request)


def _complete(request: dict) -> dict:
    try:
        normalized = validate_rc_request(request) if request["topology"] == "rc_lowpass" else validate_topology_request(request)
    except ValueError as exc:
        return {"status": "invalid_request", "message": str(exc), "supported_format": _SENTENCE_FORMAT_HINT}
    assumption = (
        "Uses the existing canonical ideal-source, open-load RC low-pass model."
        if normalized["topology"] == "rc_lowpass"
        else "Uses the existing topology's ideal-source and open-load reference model."
    )
    return {
        "status": "complete",
        "request": normalized,
        "assumptions": [assumption],
    }


def _read_range(match: re.Match[str], dimension: str, label: str) -> dict[str, float]:
    return {
        "min": _read_quantity(match.group(1), match.group(2), dimension, label),
        "max": _read_quantity(match.group(3), match.group(4), dimension, label),
    }


def _read_quantity(number: str, unit: str, dimension: str, label: str) -> float:
    normalized_unit = unit.lower().replace("μ", "µ")
    if normalized_unit not in _UNIT_SCALE[dimension]:
        raise ValueError(f"{label} has unsupported unit {unit!r}; expected a {dimension} unit")
    try:
        value = float(Decimal(number) * Decimal(str(_UNIT_SCALE[dimension][normalized_unit])))
    except DecimalException as exc:
        raise ValueError(f"{label} must be a finite positive quantity") from exc
    if value <= 0 or value == float("inf") or value != value:
        raise ValueError(f"{label} must be a finite positive quantity")
    return value


def _remove_matches(source: str, matches: list[re.Match[str]]) -> str:
    for match in reversed(matches):
        source = source[:match.start()] + " " + source[match.end():]
    return source


def _clarification(missing_fields: list[str], message: str, hint: str) -> dict:
    return {
        "status": "needs_clarification",
        "message": message,
        "missing_fields": missing_fields,
        "clarification_questions": [f"Please provide {field} explicitly." for field in missing_fields],
        "supported_format": hint,
    }


def _unsupported(text: str, message: str) -> dict:
    return {"status": "unsupported", "message": message, "unsupported_phrasing": [text],
            "supported_format": _SENTENCE_FORMAT_HINT}
