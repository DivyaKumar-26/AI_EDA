"""Deterministic, self-contained example analog designs for the initial library."""

from __future__ import annotations

from dataclasses import dataclass, field

from .ir import CircuitIR
from .rc_lowpass import build_rc_lowpass
from .topologies import TopologyDefinition, TopologyRegistry


@dataclass(frozen=True)
class ExampleDesign:
    design_id: str
    title: str
    topology: str
    description: str
    netlist: str
    bom: tuple[tuple[str, str, str], ...]
    analysis: str
    measurement_kind: str
    supply_voltage: float = 12.0
    mos_threshold: float = 2.0
    ir: CircuitIR = field(init=False, repr=False)

    def __post_init__(self) -> None:
        ir = CircuitIR.from_spice_text(self.netlist, self.title)
        object.__setattr__(self, "ir", ir)
        object.__setattr__(self, "netlist", ir.render())


_OPAMP = """.subckt CP_OPAMP INP INM OUT
BOL NRAW 0 V=limit(100000*(V(INP)-V(INM)),-14,14)
RDOM NRAW NCOMP 1k
CDOM NCOMP 0 1u
RBIAS NCOMP 0 1Meg
EOUT NBUF 0 NCOMP 0 1
ROUT NBUF OUT 50
RLEAK OUT 0 1G
.ends CP_OPAMP
"""


def _designs() -> dict[str, ExampleDesign]:
    common_source = ExampleDesign(
        "common-source-mosfet-amplifier", "Common-source MOSFET amplifier", "amplifier",
        "NMOS common-source voltage amplifier with divider gate bias and bypassed source resistor.",
        """* CircuitPilot common-source NMOS amplifier
VDD vdd 0 12
VIN in 0 DC 0 AC 1
CIN in gate 10u
RG1 vdd gate 1Meg
RG2 gate 0 390k
RD vdd out 1k
RS source 0 470
CS source 0 100u
M1 out gate source source CP_NMOS W=10u L=1u
.model CP_NMOS NMOS(VTO=2 KP=10m LAMBDA=0.02 CGSO=10n CGDO=5n)
.ac dec 100 1 100Meg
.end
""",
        (("M1", "NMOS, generic LTspice model", "1"), ("RD", "1 kΩ, 1%", "1"), ("RS", "470 Ω, 1%", "1"),
         ("RG1", "1 MΩ, 1%", "1"), ("RG2", "390 kΩ, 1%", "1"), ("CIN", "10 µF", "1"), ("CS", "100 µF", "1")),
        "AC + operating point", "mosfet_amplifier", 12.0, 2.0,
    )
    common_emitter = ExampleDesign(
        "common-emitter-bjt-amplifier", "Common-emitter BJT amplifier", "amplifier",
        "NPN common-emitter voltage amplifier with divider base bias and bypassed emitter resistor.",
        """* CircuitPilot common-emitter NPN amplifier
VCC vcc 0 12
VIN in 0 DC 0 AC 1
CIN in base 10u
RB1 vcc base 100k
RB2 base 0 22k
RC vcc out 4.7k
RE emitter 0 1k
CE emitter 0 100u
Q1 out base emitter CP_NPN
.model CP_NPN NPN(IS=1e-14 BF=150 VAF=100 CJE=10p CJC=5p TF=0.3n)
.ac dec 100 1 100Meg
.end
""",
        (("Q1", "NPN, generic LTspice model", "1"), ("RC", "4.7 kΩ, 1%", "1"), ("RE", "1 kΩ, 1%", "1"),
         ("RB1", "100 kΩ, 1%", "1"), ("RB2", "22 kΩ, 1%", "1"), ("CIN", "10 µF", "1"), ("CE", "100 µF", "1")),
        "AC + operating point", "bjt_amplifier", 12.0,
    )
    inverting = ExampleDesign(
        "inverting-opamp-amplifier", "Inverting op-amp amplifier", "amplifier",
        "Inverting gain-of-ten stage using a self-contained one-pole behavioral op-amp macro.",
        """* CircuitPilot inverting op-amp amplifier, nominal gain -10 V/V
VIN in 0 DC 0 AC 1
RIN in summing 10k
RF out summing 100k
XU1 0 summing out CP_OPAMP
.ac dec 100 10 100Meg
""" + _OPAMP + ".end\n",
        (("U1", "CP_OPAMP one-pole macro (simulation model)", "1"), ("RIN", "10 kΩ, 1%", "1"), ("RF", "100 kΩ, 1%", "1")),
        "AC + operating point", "opamp_amplifier",
    )
    noninverting = ExampleDesign(
        "non-inverting-opamp-amplifier", "Non-inverting op-amp amplifier", "amplifier",
        "Non-inverting gain-of-eleven stage using a self-contained one-pole behavioral op-amp macro.",
        """* CircuitPilot non-inverting op-amp amplifier, nominal gain 11 V/V
VIN in 0 DC 0 AC 1
RG fb 0 10k
RF out fb 100k
XU1 in fb out CP_OPAMP
.ac dec 100 10 100Meg
""" + _OPAMP + ".end\n",
        (("U1", "CP_OPAMP one-pole macro (simulation model)", "1"), ("RG", "10 kΩ, 1%", "1"), ("RF", "100 kΩ, 1%", "1")),
        "AC + operating point", "opamp_amplifier",
    )
    mos_bias = ExampleDesign(
        "mosfet-bias-network", "NMOS divider bias network", "bias_network",
        "Divider-biased NMOS with source degeneration; DC sweep checks bias across supply variation.",
        """* CircuitPilot NMOS divider bias network
VDD vdd 0 12
RG1 vdd gate 1Meg
RG2 gate 0 390k
RD vdd drain 1k
RS source 0 470
M1 drain gate source source CP_NMOS W=10u L=1u
.model CP_NMOS NMOS(VTO=2 KP=10m LAMBDA=0.02 CGSO=10n CGDO=5n)
.dc VDD 9 15 0.5
.end
""",
        (("M1", "NMOS, generic LTspice model", "1"), ("RD", "1 kΩ, 1%", "1"), ("RS", "470 Ω, 1%", "1"),
         ("RG1", "1 MΩ, 1%", "1"), ("RG2", "390 kΩ, 1%", "1")),
        "Operating point + VDD DC sweep (9–15 V)", "mosfet_bias", 12.0, 2.0,
    )
    bjt_bias = ExampleDesign(
        "bjt-bias-network", "NPN divider bias network", "bias_network",
        "Divider-biased NPN with emitter degeneration; DC sweep checks bias across supply variation.",
        """* CircuitPilot NPN divider bias network
VCC vcc 0 12
RB1 vcc base 100k
RB2 base 0 22k
RC vcc collector 4.7k
RE emitter 0 1k
Q1 collector base emitter CP_NPN
.model CP_NPN NPN(IS=1e-14 BF=150 VAF=100 CJE=10p CJC=5p TF=0.3n)
.dc VCC 9 15 0.5
.end
""",
        (("Q1", "NPN, generic LTspice model", "1"), ("RC", "4.7 kΩ, 1%", "1"), ("RE", "1 kΩ, 1%", "1"),
         ("RB1", "100 kΩ, 1%", "1"), ("RB2", "22 kΩ, 1%", "1")),
        "Operating point + VCC DC sweep (9–15 V)", "bjt_bias", 12.0,
    )
    return {design.design_id: design for design in (common_source, common_emitter, inverting, noninverting, mos_bias, bjt_bias)}


EXAMPLE_DESIGNS = _designs()

TOPOLOGY_REGISTRY = TopologyRegistry()
TOPOLOGY_REGISTRY.register(TopologyDefinition(
    "rc_lowpass",
    {"type": "object", "properties": {
        "resistance_ohm": {"type": "number", "minimum": 1e-6, "maximum": 1e12},
        "capacitance_f": {"type": "number", "minimum": 1e-15, "maximum": 1e3},
    }, "required": ["resistance_ohm", "capacitance_f"], "additionalProperties": False},
    lambda params: CircuitIR.from_spice_text(build_rc_lowpass(params["resistance_ohm"], params["capacitance_f"]).to_spice()),
    "Parameterized first-order unloaded RC low-pass.",
))
for _design in EXAMPLE_DESIGNS.values():
    TOPOLOGY_REGISTRY.register(TopologyDefinition(
        _design.design_id,
        {"type": "object", "properties": {}, "additionalProperties": False},
        lambda _params, ir=_design.ir: ir,
        _design.description,
    ))


def get_design(design_id: str) -> ExampleDesign:
    try:
        return EXAMPLE_DESIGNS[design_id]
    except KeyError as exc:
        raise ValueError(f"unknown example design: {design_id}") from exc
