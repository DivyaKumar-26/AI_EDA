"""Small, explicit LTspice batch runner and AC cutoff measurement parser."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
from typing import Callable

from .ir import CircuitIR
from .rc_lowpass import RCFilter


class SimulationError(RuntimeError):
    """Raised when LTspice or the expected AC measurement fails."""


@dataclass(frozen=True)
class ACPoint:
    frequency_hz: float
    input_magnitude: float
    output_magnitude: float
    input_real: float | None = None
    input_imag: float | None = None
    output_real: float | None = None
    output_imag: float | None = None


@dataclass(frozen=True)
class SimulationResult:
    netlist_path: Path
    raw_path: Path
    log_path: Path
    report_path: Path
    simulated_cutoff_hz: float
    analytical_cutoff_hz: float
    cutoff_error_percent: float
    simulator_executable: str


@dataclass(frozen=True)
class BatchRunResult:
    netlist_path: Path
    raw_path: Path
    log_path: Path
    op_raw_path: Path
    simulator_executable: str


def parse_ascii_ac_raw(
    raw_text: str, input_name: str = "V(in)", output_name: str = "V(out)"
) -> list[ACPoint]:
    """Extract frequency and complex input/output voltages from LTspice ASCII AC raw data."""
    lines = raw_text.splitlines()
    try:
        variables_line = next(line for line in lines if line.startswith("No. Variables:"))
        points_line = next(line for line in lines if line.startswith("No. Points:"))
        variables = int(variables_line.split(":", 1)[1].strip())
        declared_points = int(points_line.split(":", 1)[1].strip())
        variable_start = next(i for i, line in enumerate(lines) if line.strip() == "Variables:")
        values_start = next(i for i, line in enumerate(lines) if line.strip() == "Values:")
    except (StopIteration, ValueError) as exc:
        raise SimulationError("LTspice raw file has an incomplete ASCII AC header") from exc

    if declared_points < 2 or variables < 2:
        raise SimulationError("LTspice raw file must contain at least two AC points and two variables")

    variable_indices: dict[str, int] = {}
    for line in lines[variable_start + 1 : values_start]:
        match = re.match(r"\s*(\d+)\s+(\S+)", line)
        if match:
            variable_indices[match.group(2).casefold()] = int(match.group(1))
    input_index = variable_indices.get(input_name.casefold())
    if input_index is None:
        raise SimulationError(f"LTspice raw file does not contain {input_name}")
    if input_index <= 0 or input_index >= variables:
        raise SimulationError(f"LTspice raw variable index for {input_name} is invalid")
    output_index = variable_indices.get(output_name.casefold())
    if output_index is None:
        raise SimulationError(f"LTspice raw file does not contain {output_name}")
    if output_index <= 0 or output_index >= variables:
        raise SimulationError(f"LTspice raw variable index for {output_name} is invalid")
    if input_index == output_index:
        raise SimulationError("LTspice input and output variables must be distinct")

    values = lines[values_start + 1 :]
    points: list[ACPoint] = []
    cursor = 0
    for expected_index in range(declared_points):
        if cursor + variables > len(values):
            raise SimulationError("LTspice raw file ended before all declared AC points were read")
        first = re.match(r"\s*(\d+)\s+(.+?)\s*$", values[cursor])
        if first is None or int(first.group(1)) != expected_index:
            raise SimulationError(f"LTspice raw file has malformed AC point {expected_index}")
        frequency = _parse_complex(first.group(2))[0]
        input_real, input_imag = _parse_complex(values[cursor + input_index])
        output_real, output_imag = _parse_complex(values[cursor + output_index])
        input_magnitude = math.hypot(input_real, input_imag)
        output_magnitude = math.hypot(output_real, output_imag)
        if not math.isfinite(frequency) or frequency <= 0 or not math.isfinite(input_magnitude) or not math.isfinite(output_magnitude):
            raise SimulationError(f"LTspice raw point {expected_index} contains invalid values")
        points.append(ACPoint(
            frequency, input_magnitude, output_magnitude,
            input_real, input_imag, output_real, output_imag,
        ))
        cursor += variables

    if any(a.frequency_hz >= b.frequency_hz for a, b in zip(points, points[1:])):
        raise SimulationError("LTspice AC frequencies must be strictly increasing")
    return points


def _parse_complex(value_line: str) -> tuple[float, float]:
    match = re.match(r"\s*([^,\s]+)\s*,\s*([^,\s]+)", value_line)
    if match is None:
        raise SimulationError(f"Malformed complex value in LTspice raw file: {value_line.strip()!r}")
    try:
        return float(match.group(1)), float(match.group(2))
    except ValueError as exc:
        raise SimulationError("Non-numeric complex value in LTspice raw file") from exc


def measure_gain_magnitude_at_frequency(points: list[ACPoint], frequency_hz: float) -> float:
    """Measure |V(out)/V(in)| at a requested frequency, interpolating in log frequency."""
    if not math.isfinite(frequency_hz) or frequency_hz <= 0:
        raise SimulationError("Gain measurement frequency must be finite and positive")
    if len(points) < 2:
        raise SimulationError("At least two AC points are required to measure gain")
    gains: list[float] = []
    previous_frequency = 0.0
    for point in points:
        if (not math.isfinite(point.frequency_hz) or point.frequency_hz <= previous_frequency
                or not math.isfinite(point.input_magnitude) or point.input_magnitude <= 0
                or not math.isfinite(point.output_magnitude) or point.output_magnitude < 0):
            raise SimulationError("AC data contains invalid frequency or voltage magnitudes")
        gain = point.output_magnitude / point.input_magnitude
        if not math.isfinite(gain):
            raise SimulationError("AC gain magnitude is outside the representable float range")
        gains.append(gain)
        previous_frequency = point.frequency_hz
    if frequency_hz < points[0].frequency_hz or frequency_hz > points[-1].frequency_hz:
        raise SimulationError("Requested gain frequency is outside the AC sweep")
    for point, gain in zip(points, gains):
        if math.isclose(point.frequency_hz, frequency_hz, rel_tol=1e-12, abs_tol=0.0):
            return gain
    for left, right, left_gain, right_gain in zip(points, points[1:], gains, gains[1:]):
        if left.frequency_hz < frequency_hz < right.frequency_hz:
            if left_gain < 0 or right_gain < 0:
                raise SimulationError("AC gain magnitude is invalid")
            fraction = math.log(frequency_hz / left.frequency_hz) / math.log(right.frequency_hz / left.frequency_hz)
            return left_gain + fraction * (right_gain - left_gain)
    raise SimulationError("Requested gain frequency could not be bracketed by AC data")


def measure_cutoff_hz(points: list[ACPoint], target_db: float = -3.01029995664) -> float:
    """Find the unity-passband transfer cutoff, interpolating in log(f) and dB."""
    if len(points) < 2:
        raise SimulationError("At least two AC points are required to measure cutoff")
    transfer_magnitudes: list[float] = []
    for point in points:
        if not math.isfinite(point.input_magnitude) or point.input_magnitude <= 0:
            raise SimulationError("AC input has no positive magnitude")
        if not math.isfinite(point.output_magnitude) or point.output_magnitude < 0:
            raise SimulationError("AC output magnitude is invalid")
        ratio = point.output_magnitude / point.input_magnitude
        if not math.isfinite(ratio):
            raise SimulationError("AC transfer magnitude is outside the representable float range")
        transfer_magnitudes.append(ratio)
    threshold = 10.0 ** (target_db / 20.0)
    if transfer_magnitudes[0] < threshold:
        raise SimulationError("AC sweep starts below -3.0103 dB; cutoff may lie below the lower sweep limit")
    level_db = [20.0 * math.log10(value) if value > 0 else -math.inf for value in transfer_magnitudes]
    for left, right, left_db, right_db in zip(points, points[1:], level_db, level_db[1:]):
        if left_db >= target_db and right_db <= target_db:
            if left_db == right_db:
                return math.sqrt(left.frequency_hz * right.frequency_hz)
            fraction = (target_db - left_db) / (right_db - left_db)
            log_frequency = math.log(left.frequency_hz) + fraction * (math.log(right.frequency_hz) - math.log(left.frequency_hz))
            cutoff = math.exp(log_frequency)
            if math.isfinite(cutoff) and cutoff > 0:
                return cutoff
            break
    raise SimulationError("AC sweep did not contain a descending -3.0103 dB cutoff crossing")


def estimate_one_pole_passband_gain(points: list[ACPoint]) -> float:
    """Estimate DC gain from complex AC data for H(s)=G0/(1+j*w*tau).

    For a first-order low-pass, real(1/H) is constant and equals 1/G0 at every
    frequency. The median across valid raw points rejects isolated numerical outliers.
    """
    inverse_real_parts = []
    for point in points:
        complex_values = (point.input_real, point.input_imag, point.output_real, point.output_imag)
        if any(value is None or not math.isfinite(value) for value in complex_values):
            raise SimulationError("complex AC samples are required to estimate one-pole passband gain")
        source = complex(point.input_real, point.input_imag)
        output = complex(point.output_real, point.output_imag)
        if abs(source) <= 0 or abs(output) <= 0:
            continue
        transfer = output / source
        if abs(transfer) <= 0:
            continue
        inverse_transfer = 1.0 / transfer
        if math.isfinite(inverse_transfer.real) and inverse_transfer.real > 0:
            inverse_real_parts.append(inverse_transfer.real)
    if len(inverse_real_parts) < 2:
        raise SimulationError("too few valid complex AC samples to estimate passband gain")
    inverse_gain = statistics.median(inverse_real_parts)
    if not math.isfinite(inverse_gain) or inverse_gain <= 0:
        raise SimulationError("complex AC samples do not yield a finite positive passband gain")
    gain = 1.0 / inverse_gain
    if not math.isfinite(gain) or gain <= 0:
        raise SimulationError("estimated passband gain is outside the representable range")
    return gain


def measure_cutoff_relative_to_passband(
    points: list[ACPoint], passband_gain: float, direction: str = "descending"
) -> float:
    """Measure a -3 dB crossing relative to passband, in the requested sweep direction."""
    if not math.isfinite(passband_gain) or passband_gain <= 0:
        raise SimulationError("measured passband gain must be finite and positive")
    normalized = []
    for point in points:
        if not math.isfinite(point.input_magnitude) or point.input_magnitude <= 0:
            raise SimulationError("AC input has no positive magnitude")
        output = (point.output_magnitude / point.input_magnitude) / passband_gain
        if not math.isfinite(output):
            raise SimulationError("normalized AC output is outside the representable float range")
        normalized.append(ACPoint(point.frequency_hz, 1.0, output))
    if direction == "descending":
        return measure_cutoff_hz(normalized)
    if direction != "ascending":
        raise ValueError("cutoff direction must be 'ascending' or 'descending'")
    return _measure_ascending_cutoff(normalized)


def measure_peak_frequency_hz(points: list[ACPoint]) -> float:
    """Estimate the frequency of a bracketed AC magnitude peak by local log-parabolic fit."""
    if len(points) < 3:
        raise SimulationError("At least three AC points are required to measure a response peak")
    ratios = []
    for point in points:
        if not math.isfinite(point.input_magnitude) or point.input_magnitude <= 0:
            raise SimulationError("AC input has no positive magnitude")
        if not math.isfinite(point.output_magnitude) or point.output_magnitude < 0:
            raise SimulationError("AC output magnitude is invalid")
        ratio = point.output_magnitude / point.input_magnitude
        if not math.isfinite(ratio):
            raise SimulationError("AC transfer magnitude is outside the representable float range")
        ratios.append(ratio)
    peak_index = max(range(len(ratios)), key=ratios.__getitem__)
    if peak_index == 0 or peak_index == len(points) - 1 or ratios[peak_index] <= 0:
        raise SimulationError("AC sweep does not bracket a measurable interior response peak")
    x0, x1, x2 = (math.log(points[index].frequency_hz)
                  for index in (peak_index - 1, peak_index, peak_index + 1))
    y0, y1, y2 = (math.log(max(ratios[index], 1e-300))
                  for index in (peak_index - 1, peak_index, peak_index + 1))
    h1, h2 = x1 - x0, x2 - x1
    if h1 <= 0 or h2 <= 0:
        raise SimulationError("AC frequencies do not bracket an interior peak")
    a = ((y2 - y1) / h2 - (y1 - y0) / h1) / (h1 + h2)
    b = (y1 - y0) / h1 + a * h1
    if not math.isfinite(a) or not math.isfinite(b) or a >= 0:
        raise SimulationError("AC samples do not form a concave local response peak")
    offset = -b / (2.0 * a)
    if not -h1 <= offset <= h2:
        raise SimulationError("AC sweep does not bracket the fitted response peak")
    peak_hz = math.exp(x1 + offset)
    if not math.isfinite(peak_hz) or peak_hz <= 0:
        raise SimulationError("measured response peak is not finite and positive")
    return peak_hz


def _measure_ascending_cutoff(points: list[ACPoint], target_db: float = -3.01029995664) -> float:
    if len(points) < 2:
        raise SimulationError("At least two AC points are required to measure cutoff")
    level_db = []
    for point in points:
        if not math.isfinite(point.output_magnitude) or point.output_magnitude < 0:
            raise SimulationError("AC output magnitude is invalid")
        level_db.append(20.0 * math.log10(point.output_magnitude) if point.output_magnitude > 0 else -math.inf)
    if level_db[0] >= target_db:
        raise SimulationError("AC sweep starts above -3.0103 dB; high-pass cutoff may lie below the lower sweep limit")
    for left, right, left_db, right_db in zip(points, points[1:], level_db, level_db[1:]):
        if left_db <= target_db and right_db >= target_db:
            if left_db == right_db:
                return math.sqrt(left.frequency_hz * right.frequency_hz)
            fraction = (target_db - left_db) / (right_db - left_db)
            log_frequency = math.log(left.frequency_hz) + fraction * (
                math.log(right.frequency_hz) - math.log(left.frequency_hz)
            )
            cutoff = math.exp(log_frequency)
            if math.isfinite(cutoff) and cutoff > 0:
                return cutoff
    raise SimulationError("AC sweep did not contain an ascending -3.0103 dB cutoff crossing")


def discover_ltspice(explicit_path: str | None = None) -> Path:
    """Resolve an explicit executable, PATH entry, or common Windows install."""
    candidates: list[Path] = []
    if explicit_path:
        explicit = Path(explicit_path).expanduser()
        if explicit.is_dir():
            candidates.append(explicit / "LTspice.exe")
        candidates.append(explicit)
    else:
        located = shutil.which("LTspice.exe") or shutil.which("ltspice")
        if located:
            candidates.append(Path(located))
        local = os.environ.get("LOCALAPPDATA")
        home_install = Path.home() / "AppData" / "Local" / "Programs" / "ADI" / "LTspice" / "LTspice.exe"
        if local:
            candidates.append(Path(local) / "Programs" / "ADI" / "LTspice" / "LTspice.exe")
        candidates.append(home_install)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    if explicit_path:
        raise SimulationError(f"LTspice executable not found at {explicit_path}")
    raise SimulationError("LTspice executable not found; pass --ltspice with its full path")


def run_ltspice(
    circuit: RCFilter,
    output_dir: str | Path,
    executable: str | None = None,
    timeout_seconds: float = 120.0,
    runner: Callable = subprocess.run,
) -> SimulationResult:
    """Write a netlist, run LTspice batch mode, measure cutoff, and save report."""
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be finite and positive")
    directory = Path(output_dir).expanduser().resolve()
    report_path = directory / "verification-report.json"
    if report_path.exists():
        raise SimulationError(f"Refusing to overwrite existing simulation artifacts in {directory}")
    batch = run_spice(CircuitIR.from_spice_text(circuit.to_spice()), "rc_lowpass", directory, executable, timeout_seconds, runner)
    try:
        points = parse_ascii_ac_raw(batch.raw_path.read_text(encoding="ascii", errors="replace"))
    except OSError as exc:
        raise SimulationError(f"Could not read LTspice raw output: {exc}") from exc
    simulated_cutoff = measure_cutoff_hz(points)
    analytical = circuit.cutoff_hz
    error_percent = 100.0 * (simulated_cutoff - analytical) / analytical
    report = {
        "simulation_status": "completed",
        "verification_status": "not_evaluated",
        "design_pass": False,
        "simulator": {"name": "LTspice", "executable": batch.simulator_executable, "mode": "batch AC analysis"},
        "netlist": batch.netlist_path.name,
        "analysis": "AC sweep; cutoff is interpolated at -3.0103 dB transfer magnitude (V(out)/V(in)) relative to unity passband",
        "measurements": {
            "simulated_cutoff_hz": simulated_cutoff,
            "analytical_cutoff_hz": analytical,
            "cutoff_error_percent": error_percent,
            "ac_points": len(points),
        },
        "note": "Simulation completed and a cutoff was measured. No hard target constraint was supplied, so design pass/fail was not evaluated.",
    }
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return SimulationResult(batch.netlist_path, batch.raw_path, batch.log_path, report_path, simulated_cutoff, analytical, error_percent, batch.simulator_executable)


def run_spice(
    circuit: CircuitIR,
    stem: str,
    output_dir: str | Path,
    executable: str | None = None,
    timeout_seconds: float = 120.0,
    runner: Callable = subprocess.run,
) -> BatchRunResult:
    """Run an arbitrary canonical circuit IR through LTspice without interpretation."""
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be finite and positive")
    if not stem or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for character in stem):
        raise ValueError("netlist stem may contain only letters, digits, hyphens, and underscores")
    exe = discover_ltspice(executable)
    directory = Path(output_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    netlist = directory / f"{stem}.cir"
    raw = directory / f"{stem}.raw"
    log = directory / f"{stem}.log"
    op_raw = directory / f"{stem}.op.raw"
    if any(path.exists() for path in (netlist, raw, log, op_raw)):
        raise SimulationError(f"Refusing to overwrite existing LTspice artifacts in {directory}")
    netlist.write_text(circuit.render(), encoding="ascii", newline="\n")
    command = [str(exe), "-ascii", "-b", str(netlist)]
    try:
        completed = runner(command, cwd=str(directory), capture_output=True, text=True, timeout=timeout_seconds, check=False)
    except subprocess.TimeoutExpired as exc:
        raise SimulationError(f"LTspice timed out after {timeout_seconds:g} seconds") from exc
    except OSError as exc:
        raise SimulationError(f"Could not launch LTspice: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise SimulationError(f"LTspice exited with status {completed.returncode}" + (f": {detail}" if detail else ""))
    if not raw.is_file() or not log.is_file():
        missing = [path.name for path in (raw, log) if not path.is_file()]
        raise SimulationError("LTspice completed without expected output file(s): " + ", ".join(missing))
    return BatchRunResult(netlist, raw, log, op_raw, str(exe))
