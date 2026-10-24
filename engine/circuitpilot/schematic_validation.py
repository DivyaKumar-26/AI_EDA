"""Validate LTspice native netlists against the CircuitIR used to render ASC."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from .ir import CircuitElement, CircuitIR


def validate_native_netlist(circuit: CircuitIR, native_text: str) -> dict:
    """Compare native compiler component references, pin nets, and values to IR."""
    normalized_text = _normalize_ltspice_x_metadata(_join_continuations(native_text), circuit)
    native = CircuitIR.from_spice_text(normalized_text, title=circuit.title)
    expected = {element.reference.casefold(): element for element in circuit.elements}
    actual = {element.reference.casefold(): element for element in native.elements}
    checks: dict[str, dict] = {}
    for ref in sorted(expected.keys() | actual.keys()):
        ir_element, net_element = expected.get(ref), actual.get(ref)
        if ir_element is None:
            checks[ref] = {"match": False, "reason": "unexpected native component", "native": net_element.statement}
            continue
        if net_element is None:
            checks[ref] = {"match": False, "reason": "component missing from native netlist", "ir": ir_element.statement}
            continue
        pins_match = tuple(node.casefold() for node in ir_element.nodes) == tuple(node.casefold() for node in net_element.nodes)
        value_match = _normalize_body(ir_element.body) == _normalize_body(net_element.body)
        # LTspice expands a three-terminal BJT with an explicit grounded
        # substrate terminal. CircuitIR's Q syntax keeps the four-terminal
        # SPICE substrate implicit, so accept only the native `0 model` form.
        if ir_element.reference[0].upper() == "Q" and len(ir_element.nodes) == 3:
            native_body = net_element.body.split()
            if len(native_body) == len(ir_element.body.split()) + 1 and native_body[0] == "0":
                value_match = _normalize_body(" ".join(native_body[1:])) == _normalize_body(ir_element.body)
        checks[ref] = {
            "match": pins_match and value_match,
            "pins_match": pins_match,
            "value_match": value_match,
            "ir": ir_element.statement,
            "native": net_element.statement,
        }
    definition_checks = {
        "models": _compare_models(circuit.models, native.models, native.elements),
        "subcircuits": _compare_lines(circuit.subcircuits, native.subcircuits),
    }
    all_match = all(item["match"] for item in checks.values()) and all(
        item["match"] for item in definition_checks.values()
    )
    return {
        "status": "passed" if all_match else "failed",
        "all_match": all_match,
        "component_checks": checks,
        "definition_checks": definition_checks,
    }


def _join_continuations(source: str) -> str:
    lines: list[str] = []
    for raw_line in source.splitlines():
        line = raw_line.strip()
        if line.startswith("+"):
            if not lines:
                raise ValueError("native netlist begins with a continuation line")
            lines[-1] += " " + line[1:].strip()
        elif line:
            lines.append(line)
    return "\n".join(lines)


def _normalize_body(body: str) -> str:
    return " ".join(body.replace("µ", "u").replace("μ", "u").casefold().split())


def _normalize_ltspice_x_metadata(source: str, circuit: CircuitIR) -> str:
    """Strip the known LTspice 26 op-amp symbol suffix from a matching X instance.

    LTspice emits this symbol as `X§XU1 ... ;§pnba INP)INM)OUT`. Only
    normalize it when XU1 exists in the IR, its expected subcircuit is present,
    and the encoded formal-pin suffix exactly matches that subcircuit header.
    Actual instance nets and model remain in the line for normal strict checks.
    """
    expected_instances = {
        element.reference.casefold(): element
        for element in circuit.elements
        if element.reference[0].upper() == "X"
    }
    subcircuit_pins: dict[str, tuple[str, ...]] = {}
    for definition in circuit.subcircuits:
        header = definition.splitlines()[0].split()
        if len(header) >= 2 and header[0].casefold() == ".subckt":
            subcircuit_pins[header[1].casefold()] = tuple(header[2:])

    normalized_lines: list[str] = []
    for line in source.splitlines():
        if not line.startswith("X§") or ";§pnba " not in line:
            normalized_lines.append(line)
            continue
        prefix, encoded_pins = line.rsplit(" ;§pnba ", 1)
        prefix_tokens = prefix.split()
        if len(prefix_tokens) < 3 or not prefix_tokens[0].startswith("X§"):
            normalized_lines.append(line)
            continue
        reference = prefix_tokens[0][2:]
        expected = expected_instances.get(reference.casefold())
        expected_model = expected.body.split()[0].casefold() if expected is not None else None
        formal_pins = subcircuit_pins.get(expected_model or "")
        if expected is None or formal_pins is None:
            normalized_lines.append(line)
            continue
        if tuple(pin.casefold() for pin in encoded_pins.split(")")) != tuple(pin.casefold() for pin in formal_pins):
            normalized_lines.append(line)
            continue
        prefix_tokens[0] = expected.reference
        normalized_lines.append(" ".join(prefix_tokens))
    return "\n".join(normalized_lines)


def _compare_lines(expected: tuple[str, ...], actual: tuple[str, ...]) -> dict:
    normalize = lambda lines: sorted(_normalize_body(line) for line in lines)
    left, right = normalize(expected), normalize(actual)
    return {"match": left == right, "ir_count": len(left), "native_count": len(right),
            "ir": left, "native": right}


_LTSPICE_UNUSED_DEFAULT_MODELS = {
    ".model nmos nmos", ".model pmos pmos", ".model npn npn", ".model pnp pnp",
}


def _compare_models(expected: tuple[str, ...], actual: tuple[str, ...], elements: tuple[CircuitElement, ...]) -> dict:
    """Ignore only bare LTspice NMOS/PMOS defaults that no native device uses."""
    normalize = _normalize_body
    expected_lines = [normalize(line) for line in expected]
    actual_lines = [normalize(line) for line in actual]
    referenced_models: set[str] = set()
    for element in elements:
        if element.reference[0].upper() not in {"D", "J", "M", "Q", "X"}:
            continue
        body_tokens = element.body.split()
        if not body_tokens:
            continue
        # Native LTspice BJT lines can include the explicit substrate node
        # before the model name (for example `Q1 C B E 0 NPN`).
        if element.reference[0].upper() == "Q" and len(body_tokens) >= 2 and body_tokens[0] == "0":
            referenced_models.add(body_tokens[1].casefold())
        else:
            referenced_models.add(body_tokens[0].casefold())
    remaining_expected = Counter(expected_lines)
    filtered_actual: list[str] = []
    ignored: list[str] = []
    for line in actual_lines:
        if remaining_expected[line]:
            remaining_expected[line] -= 1
            filtered_actual.append(line)
        elif line in _LTSPICE_UNUSED_DEFAULT_MODELS:
            model_name = line.split()[1]
            if model_name not in referenced_models:
                ignored.append(line)
            else:
                filtered_actual.append(line)
        else:
            filtered_actual.append(line)
    missing_expected = sorted(line for line, count in remaining_expected.items() for _ in range(count))
    unexpected_actual = sorted(Counter(filtered_actual) - Counter(expected_lines))
    match = not missing_expected and not unexpected_actual
    return {"match": match, "ir_count": len(expected_lines), "native_count": len(actual_lines),
            "ir": sorted(expected_lines), "native": sorted(actual_lines),
            "ignored_unreferenced_defaults": ignored,
            "missing_from_native": missing_expected, "unexpected_in_native": unexpected_actual}


def read_native_netlist(path: str | Path) -> str:
    """Decode LTspice netlists without lossy ASCII replacement of Unicode units."""
    data = Path(path).read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252")
