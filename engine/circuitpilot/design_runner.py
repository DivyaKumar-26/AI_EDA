"""LTspice execution, measurement, BOM, and report support for example designs."""

from __future__ import annotations

import cmath
import csv
import json
import math
from pathlib import Path
import subprocess
from typing import Callable

from .designs import ExampleDesign
from .rawdata import RawPlot, parse_ascii_raw
from .simulation import SimulationError, discover_ltspice, run_spice


def run_example_design(
    design: ExampleDesign,
    output_root: str | Path,
    executable: str | None = None,
    timeout_seconds: float = 240.0,
    runner: Callable = subprocess.run,
) -> Path:
    """Generate and simulate one design in a new folder, returning its report path."""
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be finite and positive")
    exe = discover_ltspice(executable)
    root = Path(output_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    folder = root / design.design_id
    folder.mkdir()  # refuse to replace an earlier run
    netlist = folder / f"{design.design_id}.cir"
    bom_path = folder / "bom.csv"
    raw_path = folder / f"{design.design_id}.raw"
    op_raw_path = folder / f"{design.design_id}.op.raw"
    log_path = folder / f"{design.design_id}.log"
    report_path = folder / "verification-report.json"
    with bom_path.open("w", encoding="utf-8-sig", newline="") as bom_file:
        writer = csv.writer(bom_file)
        writer.writerow(("Reference", "Value / model", "Quantity"))
        writer.writerows(design.bom)

    try:
        batch = run_spice(design.ir, design.design_id, folder, str(exe), timeout_seconds, runner)
        netlist, raw_path, log_path, op_raw_path = batch.netlist_path, batch.raw_path, batch.log_path, batch.op_raw_path
        raw_plot = parse_ascii_raw(raw_path.read_text(encoding="ascii", errors="replace"))
        op_plot = parse_ascii_raw(op_raw_path.read_text(encoding="ascii", errors="replace")) if op_raw_path.is_file() else None
        if design.measurement_kind.endswith("amplifier"):
            measurements = _amplifier_measurements(design, raw_plot, op_plot)
        else:
            measurements = _bias_measurements(design, raw_plot)
        report = {
            "design_id": design.design_id,
            "title": design.title,
            "topology": design.topology,
            "description": design.description,
            "simulation_status": "completed",
            "verification_status": "measured_with_topology_checks",
            "design_pass": bool(measurements.get("topology_checks_pass", False)),
            "simulator": {"name": "LTspice", "executable": batch.simulator_executable, "mode": "batch"},
            "analysis": design.analysis,
            "artifacts": {"netlist": netlist.name, "bom": bom_path.name, "raw": raw_path.name, "log": log_path.name},
            "measurements": measurements,
            "model_limitations": _limitations(design),
            "verification_note": "design_pass covers only the documented operating-region checks, not a product-level performance specification.",
        }
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return report_path
    except subprocess.TimeoutExpired as exc:
        error = f"LTspice timed out after {timeout_seconds:g} seconds"
    except OSError as exc:
        error = f"LTspice could not complete: {exc}"
    except (SimulationError, KeyError, ValueError, ZeroDivisionError) as exc:
        error = str(exc)
    report_path.write_text(json.dumps({
        "design_id": design.design_id,
        "title": design.title,
        "simulation_status": "failed",
        "verification_status": "not_evaluated",
        "design_pass": False,
        "error": error,
        "artifacts": {"netlist": netlist.name, "bom": bom_path.name},
    }, indent=2) + "\n", encoding="utf-8")
    raise SimulationError(error)


def _limitations(design: ExampleDesign) -> list[str]:
    if design.measurement_kind == "opamp_amplifier":
        return [
            "CP_OPAMP is a generic one-pole behavioral model, not a vendor device model.",
            "The macro has a fixed open-loop gain and pole, soft output limiting, and simplified output resistance; it omits supply pins, slew rate, noise, input offset/bias current, output current limits, and detailed stability behavior.",
        ]
    if design.measurement_kind in {"mosfet_amplifier", "mosfet_bias"}:
        return ["The MOSFET uses a self-contained generic Level 1 model; real-device curves, temperature, and tolerances are not represented."]
    if design.measurement_kind in {"bjt_amplifier", "bjt_bias"}:
        return ["The BJT uses a self-contained generic model; real-device gain spread, temperature, and tolerances are not represented."]
    return []


def _node(plot: RawPlot | None, name: str) -> float:
    if plot is None or not plot.points:
        raise SimulationError("LTspice operating-point raw data is missing")
    key = f"v({name})".casefold()
    if key not in plot.points[0]:
        raise SimulationError(f"LTspice raw data does not contain node {name}")
    return plot.points[0][key].real


def _amp_curve(plot: RawPlot) -> list[tuple[float, complex, float]]:
    if "ac analysis" not in plot.plot_name.casefold():
        raise SimulationError(f"Expected AC analysis raw data, found {plot.plot_name}")
    curve: list[tuple[float, complex, float]] = []
    for point in plot.points:
        frequency = point.get("frequency")
        vin = point.get("v(in)")
        vout = point.get("v(out)")
        if frequency is None or vin is None or vout is None or abs(vin) == 0:
            raise SimulationError("AC raw data is missing frequency, V(in), or V(out)")
        ratio = vout / vin
        gain = abs(ratio)
        if frequency.real <= 0 or not math.isfinite(gain):
            raise SimulationError("AC raw data contains invalid gain or frequency")
        gain_db = 20.0 * math.log10(gain) if gain > 0 else -math.inf
        curve.append((frequency.real, ratio, gain_db))
    return curve


def _interp_crossing(left: tuple[float, complex, float], right: tuple[float, complex, float], level: float) -> float:
    f1, _, db1 = left
    f2, _, db2 = right
    fraction = (level - db1) / (db2 - db1)
    return math.exp(math.log(f1) + fraction * (math.log(f2) - math.log(f1)))


def _amplifier_measurements(design: ExampleDesign, ac_plot: RawPlot, op_plot: RawPlot | None) -> dict:
    curve = _amp_curve(ac_plot)
    peak_index = max(range(len(curve)), key=lambda index: curve[index][2])
    peak = curve[peak_index]
    cutoff_level = peak[2] - 3.01029995664
    low_cutoff = next((_interp_crossing(curve[i], curve[i + 1], cutoff_level)
                       for i in range(peak_index - 1, -1, -1)
                       if curve[i][2] <= cutoff_level <= curve[i + 1][2]), None)
    high_cutoff = next((_interp_crossing(curve[i], curve[i + 1], cutoff_level)
                        for i in range(peak_index, len(curve) - 1)
                        if curve[i][2] >= cutoff_level >= curve[i + 1][2]), None)
    at_1k = min(curve, key=lambda sample: abs(math.log(sample[0] / 1000.0)))
    measurements: dict = {
        "ac_points": len(curve),
        "peak_gain_magnitude_v_per_v": 10 ** (peak[2] / 20.0),
        "peak_gain_db": peak[2],
        "peak_gain_frequency_hz": peak[0],
        "gain_at_1khz_magnitude_v_per_v": abs(at_1k[1]),
        "gain_at_1khz_db": at_1k[2],
        "phase_at_1khz_degrees": math.degrees(cmath.phase(at_1k[1])),
        "minus_3db_low_cutoff_hz": low_cutoff,
        "minus_3db_high_cutoff_hz": high_cutoff,
        "bandwidth_hz": high_cutoff - low_cutoff if low_cutoff is not None and high_cutoff is not None else None,
    }
    if design.measurement_kind == "mosfet_amplifier":
        vg, vs, vd = _node(op_plot, "gate"), _node(op_plot, "source"), _node(op_plot, "out")
        vgs, vds = vg - vs, vd - vs
        current = vs / 470.0
        checks = {"positive_drain_current": current > 0, "vgs_above_model_threshold": vgs > design.mos_threshold,
                  "mosfet_not_in_cutoff_or_triode": vds > max(0.2, vgs - design.mos_threshold)}
        measurements.update({"operating_point_v": {"gate": vg, "source": vs, "drain": vd},
                             "bias": {"vgs_v": vgs, "vds_v": vds, "drain_current_a_estimate": current},
                             "topology_checks": checks})
    elif design.measurement_kind == "bjt_amplifier":
        vb, ve, vc = _node(op_plot, "base"), _node(op_plot, "emitter"), _node(op_plot, "out")
        vbe, vce = vb - ve, vc - ve
        current = (design.supply_voltage - vc) / 4700.0
        checks = {"positive_collector_current": current > 0, "forward_biased_base_emitter": 0.55 <= vbe <= 0.9,
                  "not_saturated": vce > 0.2}
        measurements.update({"operating_point_v": {"base": vb, "emitter": ve, "collector": vc},
                             "bias": {"vbe_v": vbe, "vce_v": vce, "collector_current_a_estimate": current},
                             "topology_checks": checks})
    elif op_plot is not None:
        measurements["operating_point_v"] = {"input": _node(op_plot, "in"), "output": _node(op_plot, "out")}
    if design.measurement_kind == "opamp_amplifier":
        expected = 10.0 if design.design_id.startswith("inverting") else 11.0
        error = 100.0 * (abs(at_1k[1]) - expected) / expected
        measurements["ideal_closed_loop_gain_magnitude_v_per_v"] = expected
        measurements["gain_at_1khz_error_percent"] = error
        measurements["topology_checks"] = {"gain_at_1khz_within_5_percent_of_resistor_ratio": abs(error) <= 5.0}
    if "topology_checks" in measurements:
        measurements["topology_checks_pass"] = all(measurements["topology_checks"].values())
    else:
        measurements["topology_checks_pass"] = True
    return measurements


def _bias_measurements(design: ExampleDesign, dc_plot: RawPlot) -> dict:
    if "dc" not in dc_plot.plot_name.casefold():
        raise SimulationError(f"Expected DC sweep raw data, found {dc_plot.plot_name}")
    supply_key = "v(vdd)" if design.measurement_kind == "mosfet_bias" else "v(vcc)"
    supply_values = [point.get(supply_key) for point in dc_plot.points]
    if any(value is None for value in supply_values):
        raise SimulationError(f"DC raw data does not contain swept supply {supply_key}")
    nominal_point = min(dc_plot.points, key=lambda point: abs(point[supply_key].real - design.supply_voltage))
    sweep = []
    for point in dc_plot.points:
        supply = point[supply_key].real
        if design.measurement_kind == "mosfet_bias":
            vg, vs, vd = (point.get("v(gate)"), point.get("v(source)"), point.get("v(drain)"))
            if vg is None or vs is None or vd is None:
                raise SimulationError("DC raw data is missing MOSFET bias nodes")
            vgs, vds = vg.real - vs.real, vd.real - vs.real
            current = vs.real / 470.0
            active = current > 0 and vgs > design.mos_threshold and vds > max(0.2, vgs - design.mos_threshold)
            sweep.append({"supply_v": supply, "drain_current_a_estimate": current, "vds_v": vds, "active_region": active})
        else:
            vb, ve, vc = (point.get("v(base)"), point.get("v(emitter)"), point.get("v(collector)"))
            if vb is None or ve is None or vc is None:
                raise SimulationError("DC raw data is missing BJT bias nodes")
            vbe, vce = vb.real - ve.real, vc.real - ve.real
            current = (supply - vc.real) / 4700.0
            active = current > 0 and 0.55 <= vbe <= 0.9 and vce > 0.2
            sweep.append({"supply_v": supply, "collector_current_a_estimate": current, "vbe_v": vbe, "vce_v": vce, "active_region": active})
    if design.measurement_kind == "mosfet_bias":
        vg, vs, vd = (nominal_point[key].real for key in ("v(gate)", "v(source)", "v(drain)"))
        vgs, vds, current = vg - vs, vd - vs, vs / 470.0
        checks = {"positive_drain_current": current > 0, "vgs_above_model_threshold": vgs > design.mos_threshold,
                  "mosfet_in_saturation": vds > max(0.2, vgs - design.mos_threshold)}
        bias = {"vgs_v": vgs, "vds_v": vds, "drain_current_a_estimate": current}
    else:
        vb, ve, vc = (nominal_point[key].real for key in ("v(base)", "v(emitter)", "v(collector)"))
        vbe, vce, current = vb - ve, vc - ve, (design.supply_voltage - vc) / 4700.0
        checks = {"positive_collector_current": current > 0, "forward_biased_base_emitter": 0.55 <= vbe <= 0.9,
                  "bjt_not_saturated": vce > 0.2}
        bias = {"vbe_v": vbe, "vce_v": vce, "collector_current_a_estimate": current}
    sweep_pass = all(sample["active_region"] for sample in sweep)
    checks["active_region_across_entire_supply_sweep"] = sweep_pass
    bias_pass = all(checks.values())
    return {
        "sweep_points": len(sweep),
        "sweep_supply_min_v": min(sample["supply_v"] for sample in sweep),
        "sweep_supply_max_v": max(sample["supply_v"] for sample in sweep),
        "nominal_supply_v": nominal_point[supply_key].real,
        "nominal_bias": bias,
        "sweep_summary": {"active_region_points": sum(sample["active_region"] for sample in sweep),
                          "total_points": len(sweep), "min_current_a": min(sample.get("drain_current_a_estimate", sample.get("collector_current_a_estimate")) for sample in sweep),
                          "max_current_a": max(sample.get("drain_current_a_estimate", sample.get("collector_current_a_estimate")) for sample in sweep)},
        "topology_checks": checks,
        "topology_checks_pass": bias_pass,
    }
