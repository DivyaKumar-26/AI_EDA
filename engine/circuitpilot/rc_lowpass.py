"""Validated first-order RC low-pass representation and SPICE rendering."""

from __future__ import annotations

from dataclasses import dataclass
import math
import sys

from .ir import CircuitElement, CircuitIR


def _positive_finite(value: float, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite positive number") from exc
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{label} must be a finite positive number")
    return result


@dataclass(frozen=True)
class RCFilter:
    """Canonical IR for an unloaded RC low-pass, with output across C."""

    resistance_ohm: float
    capacitance_f: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "resistance_ohm", _positive_finite(self.resistance_ohm, "resistance_ohm"))
        object.__setattr__(self, "capacitance_f", _positive_finite(self.capacitance_f, "capacitance_f"))

    @property
    def cutoff_hz(self) -> float:
        return cutoff_frequency_hz(self.resistance_ohm, self.capacitance_f)

    def to_spice(self) -> str:
        """Render deterministic LTspice-compatible SPICE for AC analysis."""
        ir = CircuitIR(
            title="CircuitPilot first-order RC low-pass",
            elements=(
                CircuitElement.from_statement("V1 in 0 AC 1"),
                CircuitElement.from_statement(f"R1 in out {format_spice_number(self.resistance_ohm)}"),
                CircuitElement.from_statement(f"C1 out 0 {format_spice_number(self.capacitance_f)}"),
            ),
            directives=(".ac dec 100 1 1Meg",),
        )
        return ir.render()


def cutoff_frequency_hz(resistance_ohm: float, capacitance_f: float) -> float:
    """Return ideal unloaded cutoff, 1/(2*pi*R*C), after validating inputs."""
    resistance = _positive_finite(resistance_ohm, "resistance_ohm")
    capacitance = _positive_finite(capacitance_f, "capacitance_f")
    # Work in log space so finite inputs cannot overflow/underflow an
    # intermediate product and silently produce a false 0 Hz or exception.
    log_cutoff = -math.log(2.0 * math.pi) - math.log(resistance) - math.log(capacitance)
    smallest_positive = math.nextafter(0.0, 1.0)
    if log_cutoff > math.log(sys.float_info.max) or log_cutoff < math.log(smallest_positive):
        raise ValueError("cutoff frequency is outside the representable positive float range")
    try:
        result = math.exp(log_cutoff)
    except OverflowError as exc:
        raise ValueError("cutoff frequency is outside the representable positive float range") from exc
    if not math.isfinite(result) or result <= 0:
        raise ValueError("cutoff frequency is outside the representable positive float range")
    return result


def format_spice_number(value: float) -> str:
    """Stable, locale-independent decimal representation without unit suffixes."""
    number = _positive_finite(value, "SPICE value")
    return format(number, ".12g")


def build_rc_lowpass(resistance_ohm: float, capacitance_f: float) -> RCFilter:
    """Build the canonical circuit representation from explicit SI values."""
    return RCFilter(resistance_ohm=resistance_ohm, capacitance_f=capacitance_f)
