"""Render LTspice schematics from the canonical CircuitIR component pins."""

from __future__ import annotations

from dataclasses import dataclass
import re

from .ir import CircuitElement, CircuitIR


@dataclass(frozen=True)
class SymbolSpec:
    name: str
    pin_offsets: tuple[tuple[int, int], ...]
    pin_names: tuple[str, ...]
    prefix: str


SYMBOLS = {
    "R": SymbolSpec("res", ((16, 16), (16, 96)), ("A", "B"), "R"),
    "C": SymbolSpec("cap", ((16, 0), (16, 64)), ("A", "B"), "C"),
    "L": SymbolSpec("ind", ((16, 16), (16, 96)), ("A", "B"), "L"),
    "V": SymbolSpec("voltage", ((0, 16), (0, 96)), ("+", "-"), "V"),
    "I": SymbolSpec("current", ((0, 0), (0, 80)), ("+", "-"), "I"),
    "Q": SymbolSpec("npn", ((64, 0), (0, 48), (64, 96)), ("C", "B", "E"), "Q"),
    "M": SymbolSpec("nmos", ((48, 0), (0, 80), (48, 96)), ("D", "G", "S"), "M"),
    "X": SymbolSpec("CP_OPAMP", ((-32, 80), (-32, 48), (32, 64)), ("INP", "INM", "OUT"), "X"),
}

CP_OPAMP_SYMBOL = """Version 4
SymbolType CELL
LINE Normal -32 32 32 64
LINE Normal -32 96 32 64
LINE Normal -32 32 -32 96
WINDOW 0 48 32 Left 2
WINDOW 3 48 80 Left 2
SYMATTR Value CP_OPAMP
SYMATTR Prefix X
SYMATTR Description CircuitPilot self-contained one-pole op-amp macro
PIN -32 80 NONE 0
PINATTR PinName INP
PINATTR SpiceOrder 1
PIN -32 48 NONE 0
PINATTR PinName INM
PINATTR SpiceOrder 2
PIN 32 64 NONE 0
PINATTR PinName OUT
PINATTR SpiceOrder 3
"""


def render_asc(circuit: CircuitIR, sheet_title: str | None = None) -> str:
    """Render an editable ASC with every IR pin assigned its canonical named net.

    Each symbol pin is placed on a labeled LTspice flag. Shared IR net names
    therefore produce shared electrical connectivity without a second wire graph.
    """
    lines = ["Version 4", "SHEET 1 2400 2000"]
    if sheet_title:
        lines.append(f"TEXT 64 32 Left 2 ;{_safe_text(sheet_title)}")

    # Spread symbols on a grid so ordinary devices and the custom macro do not
    # overlap. Net flags provide direct, inspectable electrical connections.
    for index, element in enumerate(circuit.elements):
        spec, model_value, spice_line = _symbol_for(element)
        x = 256 + (index % 4) * 480
        y = 256 + (index // 4) * 320
        lines.extend((f"SYMBOL {spec.name} {x} {y} R0", f"SYMATTR InstName {element.reference}",
                      f"SYMATTR Value {model_value}"))
        if spice_line:
            lines.append(f"SYMATTR SpiceLine {spice_line}")
        symbol_nodes = element.nodes[:len(spec.pin_offsets)]
        for pin_offset, node in zip(spec.pin_offsets, symbol_nodes, strict=True):
            px, py = x + pin_offset[0], y + pin_offset[1]
            flag_x = px + 64
            lines.append(f"WIRE {flag_x} {py} {px} {py}")
            lines.append(f"FLAG {flag_x} {py} {_safe_net_name(node)}")

    text_x, text_y = 64, 1450
    for directive in (*circuit.models, *[line for block in circuit.subcircuits for line in block.splitlines()], *circuit.directives):
        lines.append(f"TEXT {text_x} {text_y} Left 2 !{directive}")
        text_y += 32
    return "\n".join(lines) + "\n"


def required_support_files(circuit: CircuitIR) -> dict[str, str]:
    """Return local symbol files needed by the rendered schematic."""
    if any(element.reference.upper().startswith("X") for element in circuit.elements):
        return {"CP_OPAMP.asy": CP_OPAMP_SYMBOL}
    return {}


def connectivity_manifest(circuit: CircuitIR) -> dict[str, tuple[str, ...]]:
    """Describe the exact IR net assignment for every symbol pin, keyed by ref."""
    manifest: dict[str, tuple[str, ...]] = {}
    for element in circuit.elements:
        _symbol_for(element)  # validates pin/model mapping used by the exporter
        manifest[element.reference] = element.nodes
    return manifest


def connectivity_pin_map(circuit: CircuitIR) -> dict[str, dict]:
    """Map LTspice symbol SpiceOrder pin names to IR nets for audit and tests."""
    mapping = {}
    for element in circuit.elements:
        spec, _, _ = _symbol_for(element)
        mapping[element.reference] = {
            "symbol": spec.name,
            "pins": [{"spice_order": index, "pin_name": name, "net": element.nodes[index - 1]}
                     for index, name in enumerate(spec.pin_names, start=1)],
        }
        if element.reference[0].upper() == "M":
            mapping[element.reference]["implicit_bulk_tied_to"] = element.nodes[2]
    return mapping


def _symbol_for(element: CircuitElement) -> tuple[SymbolSpec, str, str]:
    kind = element.reference[0].upper()
    try:
        spec = SYMBOLS[kind]
    except KeyError as exc:
        raise ValueError(f"ASC export does not support component kind {kind!r} ({element.reference})") from exc
    body_tokens = element.body.split()
    if kind == "M":
        # LTspice's standard NMOS symbol emits a 3-terminal instance whose
        # implicit bulk is the source. The IR must express that same tie.
        if len(element.nodes) != 4 or element.nodes[2].casefold() != element.nodes[3].casefold():
            raise ValueError(f"{element.reference}: the standard 3-pin NMOS symbol requires bulk tied to source")
        if not body_tokens:
            raise ValueError(f"{element.reference}: missing MOS model name")
        return spec, body_tokens[0], " ".join(body_tokens[1:])
    if len(element.nodes) != len(spec.pin_offsets):
        raise ValueError(f"{element.reference}: ASC symbol has {len(spec.pin_offsets)} pins but IR has {len(element.nodes)}")
    if kind in {"Q", "X"}:
        if kind == "X" and body_tokens[0].casefold() != "cp_opamp":
            raise ValueError(f"{element.reference}: no ASC symbol is registered for subcircuit {body_tokens[0]!r}")
        return spec, body_tokens[0], " ".join(body_tokens[1:])
    return spec, element.body, ""


def _safe_net_name(value: str) -> str:
    if value == "0":
        return value
    if not re.fullmatch(r"[A-Za-z0-9_.$-]+", value):
        raise ValueError(f"unsupported ASC net label {value!r}")
    return value


def _safe_text(value: str) -> str:
    return value.replace("\r", " ").replace("\n", " ")
