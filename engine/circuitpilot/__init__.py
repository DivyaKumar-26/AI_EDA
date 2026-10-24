"""Public synthesis, IR, schematic, and simulation API for CircuitPilot."""

from .ir import CircuitElement, CircuitIR
from .plain_language import parse_plain_language_request
from .rc_lowpass import RCFilter, build_rc_lowpass, cutoff_frequency_hz
from .rc_request import NoFeasibleCandidateError, RCSynthesisResult, synthesize_rc_request, validate_rc_request
from .rc_request_simulation import RCRequestRun, simulate_rc_request
from .schematic import render_asc
from .simulation import SimulationError
from .topology_requests import (
    TopologyRequestResult,
    load_behavioral_request,
    simulate_topology_request,
    synthesize_behavioral_request,
    validate_behavioral_request,
)

__all__ = [
    "CircuitElement",
    "CircuitIR",
    "RCFilter",
    "build_rc_lowpass",
    "cutoff_frequency_hz",
    "NoFeasibleCandidateError",
    "RCSynthesisResult",
    "synthesize_rc_request",
    "validate_rc_request",
    "parse_plain_language_request",
    "TopologyRequestResult",
    "load_behavioral_request",
    "validate_behavioral_request",
    "synthesize_behavioral_request",
    "render_asc",
    "RCRequestRun",
    "simulate_rc_request",
    "simulate_topology_request",
    "SimulationError",
]
