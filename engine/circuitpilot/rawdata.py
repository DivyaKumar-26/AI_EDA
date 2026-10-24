"""Minimal reader for LTspice ASCII real and complex raw plots."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re

from .simulation import SimulationError, _parse_complex


@dataclass(frozen=True)
class RawPlot:
    plot_name: str
    flags: str
    variables: tuple[str, ...]
    points: tuple[dict[str, complex], ...]


def parse_ascii_raw(raw_text: str) -> RawPlot:
    lines = raw_text.splitlines()
    try:
        plot_line = next(line for line in lines if line.startswith("Plotname:"))
        flags_line = next(line for line in lines if line.startswith("Flags:"))
        variable_count = int(next(line for line in lines if line.startswith("No. Variables:")).split(":", 1)[1])
        point_count = int(next(line for line in lines if line.startswith("No. Points:")).split(":", 1)[1])
        variable_start = next(i for i, line in enumerate(lines) if line.strip() == "Variables:")
        values_start = next(i for i, line in enumerate(lines) if line.strip() == "Values:")
    except (StopIteration, ValueError) as exc:
        raise SimulationError("LTspice raw file has an incomplete ASCII header") from exc
    variables: list[str] = []
    for line in lines[variable_start + 1 : values_start]:
        match = re.match(r"\s*(\d+)\s+(\S+)", line)
        if match:
            variables.append(match.group(2))
    if variable_count < 1 or len(variables) != variable_count or point_count < 1:
        raise SimulationError("LTspice raw file has inconsistent variable or point counts")

    values = lines[values_start + 1 :]
    cursor = 0
    points: list[dict[str, complex]] = []
    for expected_index in range(point_count):
        if cursor + variable_count > len(values):
            raise SimulationError("LTspice raw file ended before all declared points were read")
        first = re.match(r"\s*(\d+)\s+(.+?)\s*$", values[cursor])
        if first is None or int(first.group(1)) != expected_index:
            raise SimulationError(f"LTspice raw file has malformed point {expected_index}")
        parsed = [_parse_value(first.group(2))]
        for line in values[cursor + 1 : cursor + variable_count]:
            parsed.append(_parse_value(line))
        points.append(dict(zip((name.casefold() for name in variables), parsed)))
        cursor += variable_count
    return RawPlot(plot_line.split(":", 1)[1].strip(), flags_line.split(":", 1)[1].strip(), tuple(variables), tuple(points))


def _parse_value(value: str) -> complex:
    try:
        if "," in value:
            real, imag = _parse_complex(value)
        else:
            real, imag = float(value.strip()), 0.0
    except ValueError as exc:
        raise SimulationError(f"Invalid numeric value in LTspice raw file: {value.strip()!r}") from exc
    if not math.isfinite(real) or not math.isfinite(imag):
        raise SimulationError("LTspice raw file contains a non-finite value")
    return complex(real, imag)
