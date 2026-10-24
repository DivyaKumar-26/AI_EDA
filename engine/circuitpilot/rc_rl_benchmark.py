"""Target-aware tabular RL benchmark over the tracked LTspice-measured RC grid.

This is an offline policy experiment. It does not call LTspice and is not integrated
with the behavioral design CLI. Candidate acceptance always uses the saved LTspice
measurements, never the analytical estimate used to rank the conventional baseline.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import random
from typing import Any

from circuitpilot.simulation import SimulationError, measure_cutoff_hz, parse_ascii_ac_raw


R_VALUES_OHM = (680.0, 1000.0, 1500.0)
C_VALUES_F = (47e-9, 100e-9, 220e-9)
ACTIONS = ("R_UP", "R_DOWN", "C_UP", "C_DOWN")
GLOBAL_ACTIONS = tuple(f"PROPOSE_R{ri}_C{ci}" for ri in range(3) for ci in range(3))
DEFAULT_DATASET = Path(__file__).resolve().parents[2] / "output" / "rl_rc_v1" / "benchmark.json"
TRUSTED_BENCHMARK_SHA256 = "ac3f70189d75dfcb658c17c8fe0448ebd4528f6cd23cca2976e9035a162d0ef2"
TRUSTED_RAW_SHA256_BY_STATE = {
    "0,0": "903f1ada5fe115122e233203d2f385fdcb82762e37482cea8a5293592a76ff27",
    "0,1": "dd47ba13b1cead5537d1ae9932df6484f3adb0a3e6172b6263a9ceafd9a3d772",
    "0,2": "3d508d090739e3dbc2b75d8657b60439d32f6127273a81fb5a762af0850677f4",
    "1,0": "19bf26e360ef55934d7119a7c0f3e8f229babf02a8d68ae61e4cbc387f74ffb2",
    "1,1": "891828afd499bc32e61d78dc2763b5667b6a3bc7b95b3975081d51f8e6677961",
    "1,2": "18eb6d57e0416ebf4f833d4f47f066ecf033ac0ab31497de8428e9fd53fecd82",
    "2,0": "23ad20a954ecd9d3f7e7f374dcae3dbe9f3bc3e1d4e6e6e1b4eb11f098f4f3d6",
    "2,1": "e2e8e4b201aa5e092eed6b9ecf0841e5ded282e78b92ee3b240267910c6dbbf0",
    "2,2": "eaa5b1f4d7680670978769a1532d2073451c07e2bbdda6acca28461dc777d387",
}

TRAIN_TASKS = (
    {"task_id": "train_low", "target_hz": 482.259522972956, "tolerance_percent": 1.0},
    {"task_id": "train_mid", "target_hz": 1591.4870757015626, "tolerance_percent": 1.0},
    {"task_id": "train_high", "target_hz": 3386.25088192633, "tolerance_percent": 1.0},
    {"task_id": "train_upper", "target_hz": 4979.550041232938, "tolerance_percent": 1.0},
)
HELD_OUT_TASKS = (
    {"task_id": "heldout_723", "cutoff_range_hz": [720.0, 730.0]},
    {"task_id": "heldout_1061", "cutoff_range_hz": [1059.0, 1062.0]},
    {"task_id": "heldout_1064", "cutoff_range_hz": [1063.0, 1065.0]},
    {"task_id": "heldout_2257", "cutoff_range_hz": [2255.0, 2260.0]},
    {"task_id": "heldout_2340", "cutoff_range_hz": [2338.0, 2343.0]},
)

_ERROR_BAND_LIMITS = (0.80, 0.97, 0.99, 1.01, 1.03, 1.25)
TRAINING_GUARD_FRACTION = 0.05
TRAINING_TOLERANCE_PERCENT = 1.0
_TRAINING_TARGET_SEED_SALT = 0x52434C50


def load_measured_grid(dataset_path: str | Path = DEFAULT_DATASET) -> tuple[dict[tuple[int, int], float], dict]:
    """Load the pinned nine-point dataset and validate each pinned LTspice raw trace."""
    source = Path(dataset_path).expanduser().resolve()
    try:
        benchmark_bytes = source.read_bytes()
        benchmark = json.loads(benchmark_bytes.decode("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not load measured-grid benchmark data: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ValueError("measured-grid benchmark is not valid UTF-8") from exc
    canonical_benchmark = json.dumps(benchmark, sort_keys=True, separators=(",", ":"))
    file_digest = hashlib.sha256(canonical_benchmark.encode("utf-8")).hexdigest()
    if file_digest != TRUSTED_BENCHMARK_SHA256:
        raise ValueError("measured-grid benchmark content does not match the trusted pinned SHA-256")
    if benchmark.get("benchmark_id") != "rc_lowpass_fixed_target_grid_v1":
        raise ValueError("measured-grid source must be the tracked rc_lowpass_fixed_target_grid_v1 benchmark")
    rows = benchmark.get("measured_candidates")
    if not isinstance(rows, list) or len(rows) != 9:
        raise ValueError("measured-grid source must contain exactly nine candidate rows")
    canonical = json.dumps(rows, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if digest != benchmark.get("measurement_dataset_sha256"):
        raise ValueError("measured-grid dataset hash does not match its recorded provenance")

    expected_states = {(ri, ci) for ri in range(3) for ci in range(3)}
    measured: dict[tuple[int, int], float] = {}
    for row in rows:
        try:
            state = tuple(row["state"])
            ri, ci = state
            resistance = float(row["resistance_ohm"])
            capacitance = float(row["capacitance_f"])
            cutoff = float(row["simulated_cutoff_hz"])
            report_relative = str(row["simulation_report"]).replace("\\", "/")
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("measured-grid row is missing a valid state, component value, cutoff, or report path") from exc
        if state in measured or (ri, ci) not in expected_states:
            raise ValueError("measured-grid states must contain each 3x3 state exactly once")
        if resistance != R_VALUES_OHM[ri] or capacitance != C_VALUES_F[ci]:
            raise ValueError("measured-grid component values differ from the fixed tracked RC lattice")
        if not math.isfinite(cutoff) or cutoff <= 0:
            raise ValueError("measured cutoff values must be finite and positive")
        report_path = (source.parent / report_relative).resolve()
        if not report_path.is_relative_to(source.parent):
            raise ValueError("simulation report path escapes the measured-grid artifact folder")
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report_cutoff = float(report["measurements"]["simulated_cutoff_hz"])
            raw_path = report_path.parent / "rc_lowpass.raw"
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"measured-grid evidence is unavailable or malformed: {report_path}") from exc
        if report.get("simulation_status") != "completed" or report.get("simulator", {}).get("name") != "LTspice":
            raise ValueError(f"candidate evidence is not a completed LTspice simulation: {report_path}")
        try:
            raw_bytes = raw_path.read_bytes()
            raw_digest = hashlib.sha256(raw_bytes).hexdigest()
            expected_raw_digest = TRUSTED_RAW_SHA256_BY_STATE[f"{ri},{ci}"]
            if raw_digest != expected_raw_digest:
                raise ValueError(f"LTspice raw file does not match its trusted pinned SHA-256: {raw_path}")
            raw_points = parse_ascii_ac_raw(raw_bytes.decode("utf-8", errors="strict"))
            parsed_cutoff = measure_cutoff_hz(raw_points)
        except (OSError, UnicodeDecodeError, KeyError, ValueError, SimulationError) as exc:
            raise ValueError(f"pinned LTspice raw data is unavailable or invalid for {report_path}: {exc}") from exc
        if report_cutoff != cutoff or parsed_cutoff != cutoff:
            raise ValueError(f"raw trace, report, and benchmark cutoff do not match for {report_path}")
        measured[(ri, ci)] = cutoff
    if set(measured) != expected_states:
        raise ValueError("measured-grid source does not cover all nine component states")
    provenance = {
        "source_benchmark": source.relative_to(source.parents[2]).as_posix(),
        "trusted_benchmark_content_sha256": file_digest,
        "measurement_dataset_sha256": digest,
        "raw_trace_hashes_pinned": True,
        "simulator": benchmark.get("simulator"),
        "candidate_count": len(measured),
        "raw_measurements_validated": "pinned SHA-256 and parsed cutoff matched benchmark and report",
    }
    return measured, provenance


def _training_target_intervals(
    measured: dict[tuple[int, int], float], guard_fraction: float = TRAINING_GUARD_FRACTION,
) -> list[tuple[float, float]]:
    """Return measured-span intervals outside multiplicatively guarded test ranges."""
    if not 0 <= guard_fraction < 1:
        raise ValueError("training target guard fraction must be in [0, 1)")
    low, high = min(measured.values()), max(measured.values())
    forbidden = sorted(
        (max(low, task["cutoff_range_hz"][0] / (1.0 + guard_fraction)),
         min(high, task["cutoff_range_hz"][1] * (1.0 + guard_fraction)))
        for task in HELD_OUT_TASKS
    )
    merged: list[list[float]] = []
    for start, end in forbidden:
        if start > end:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    allowed: list[tuple[float, float]] = []
    cursor = low
    for start, end in merged:
        if cursor < start:
            allowed.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < high:
        allowed.append((cursor, high))
    allowed = [(start, end) for start, end in allowed if start > 0 and end > start]
    if not allowed:
        raise ValueError("no measured cutoff span remains for training targets outside guarded test ranges")
    return allowed


def generate_training_tasks(
    measured: dict[tuple[int, int], float], count: int, seed: int,
) -> list[dict]:
    """Create an explicit seeded log-uniform training-target manifest outside test guards."""
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError("training target count must be a positive integer")
    intervals = _training_target_intervals(measured)
    log_intervals = [(math.log(low), math.log(high)) for low, high in intervals]
    lengths = [end - start for start, end in log_intervals]
    total_log_length = sum(lengths)
    rng = random.Random(seed ^ _TRAINING_TARGET_SEED_SALT)
    targets: list[dict] = []
    for index in range(count):
        position = rng.random() * total_log_length
        for (start, end), length in zip(log_intervals, lengths):
            if position <= length:
                target = math.exp(start + position)
                break
            position -= length
        else:
            target = math.exp(log_intervals[-1][1])
        targets.append({
            "task_id": f"train_sample_{index:05d}",
            "target_hz": target,
            "tolerance_percent": TRAINING_TOLERANCE_PERCENT,
        })
    return targets


def _error_band(measured_hz: float, target_hz: float) -> int:
    ratio = measured_hz / target_hz
    for band, boundary in enumerate(_ERROR_BAND_LIMITS):
        if ratio < boundary:
            return band
    return len(_ERROR_BAND_LIMITS)


def _policy_state(state: tuple[int, int], measured: dict[tuple[int, int], float], target_hz: float) -> tuple[int, int, int]:
    return state[0], state[1], _error_band(measured[state], target_hz)


def _within_target_range(measured_hz: float, low_hz: float, high_hz: float) -> bool:
    return low_hz <= measured_hz <= high_hz


def _train_q_policy(
    measured: dict[tuple[int, int], float],
    seed: int,
    episodes: int,
    max_steps: int,
    alpha: float = 0.25,
    gamma: float = 0.9,
    epsilon_start: float = 1.0,
    epsilon_min: float = 0.05,
    epsilon_decay: float = 0.997,
    training_tasks: list[dict] | None = None,
) -> dict[tuple[int, int, int], list[float]]:
    if isinstance(episodes, bool) or episodes < 1 or max_steps < 1:
        raise ValueError("training episodes and max_steps must be positive integers")
    rng = random.Random(seed)
    states = tuple(measured)
    task_schedule = training_tasks or list(TRAIN_TASKS)
    if len(task_schedule) != episodes:
        raise ValueError("training task manifest length must equal the configured episode count")
    q = {(ri, ci, band): [0.0] * len(ACTIONS)
         for ri in range(3) for ci in range(3) for band in range(len(_ERROR_BAND_LIMITS) + 1)}
    epsilon = epsilon_start
    for task in task_schedule:
        target = task["target_hz"]
        tolerance = task["tolerance_percent"] / 100.0
        low, high = target * (1.0 - tolerance), target * (1.0 + tolerance)
        state = rng.choice(states)
        for _ in range(max_steps):
            state_key = _policy_state(state, measured, target)
            if rng.random() < epsilon:
                action = rng.randrange(len(ACTIONS))
            else:
                action = _argmax(q[state_key])
            next_state = _transition(state, action)
            success = _within_target_range(measured[next_state], low, high)
            reward = abs(math.log(measured[state] / target)) - abs(math.log(measured[next_state] / target)) - 0.02
            if success:
                reward += 1.0
            next_key = _policy_state(next_state, measured, target)
            future = 0.0 if success else max(q[next_key])
            q[state_key][action] += alpha * (reward + gamma * future - q[state_key][action])
            state = next_state
            if success:
                break
        epsilon = max(epsilon_min, epsilon * epsilon_decay)
    return q


def _argmax(values: list[float]) -> int:
    return max(range(len(values)), key=lambda index: values[index])


def _transition(state: tuple[int, int], action: int) -> tuple[int, int]:
    ri, ci = state
    if action == 0:
        ri += 1
    elif action == 1:
        ri -= 1
    elif action == 2:
        ci += 1
    else:
        ci -= 1
    return (ri, ci) if 0 <= ri < 3 and 0 <= ci < 3 else state


def _global_action_candidate(action: int) -> tuple[int, int]:
    if isinstance(action, bool) or not 0 <= action < len(GLOBAL_ACTIONS):
        raise ValueError("global proposal action index is out of range")
    return divmod(action, len(C_VALUES_F))


def _candidate_mask(state: tuple[int, int]) -> int:
    return 1 << (state[0] * len(C_VALUES_F) + state[1])


def _available_global_actions(visited_mask: int) -> list[int]:
    return [action for action in range(len(GLOBAL_ACTIONS))
            if not visited_mask & _candidate_mask(_global_action_candidate(action))]


def _global_policy_state(
    state: tuple[int, int], measured: dict[tuple[int, int], float], target_hz: float, visited_mask: int,
) -> tuple[int, int, int, int]:
    return state[0], state[1], _error_band(measured[state], target_hz), visited_mask


def _train_global_q_policy(
    measured: dict[tuple[int, int], float],
    seed: int,
    training_episodes: int,
    max_steps: int,
    training_tasks: list[dict],
    alpha: float = 0.25,
    gamma: float = 0.9,
    epsilon_start: float = 1.0,
    epsilon_min: float = 0.05,
    epsilon_decay: float = 0.997,
) -> dict[tuple[int, int, int, int], list[float]]:
    """Train direct-to-grid proposals with visited candidates masked from each episode."""
    if isinstance(training_episodes, bool) or training_episodes < 1 or max_steps < 1:
        raise ValueError("training episodes and max_steps must be positive integers")
    if len(training_tasks) != training_episodes:
        raise ValueError("training task manifest length must equal the configured episode count")
    rng = random.Random(seed)
    states = tuple(measured)
    q: dict[tuple[int, int, int, int], list[float]] = {}
    epsilon = epsilon_start
    for task in training_tasks:
        state = rng.choice(states)
        _train_global_q_episode(
            task, state, measured, q, rng, epsilon, max_steps,
            alpha=alpha, gamma=gamma,
        )
        epsilon = max(epsilon_min, epsilon * epsilon_decay)
    return q


def _train_global_q_episode(
    task: dict,
    start: tuple[int, int],
    measured: dict[tuple[int, int], float],
    q: dict[tuple[int, int, int, int], list[float]],
    rng: random.Random,
    epsilon: float,
    max_steps: int,
    *,
    alpha: float = 0.25,
    gamma: float = 0.9,
) -> None:
    """Update one global-policy episode, ending immediately if START already passes."""
    target = task["target_hz"]
    tolerance = task["tolerance_percent"] / 100.0
    low, high = target * (1.0 - tolerance), target * (1.0 + tolerance)
    if _within_target_range(measured[start], low, high):
        return

    state = start
    visited_mask = _candidate_mask(state)
    for _ in range(max_steps):
        available = _available_global_actions(visited_mask)
        if not available:
            break
        state_key = _global_policy_state(state, measured, target, visited_mask)
        values = q.setdefault(state_key, [0.0] * len(GLOBAL_ACTIONS))
        action = rng.choice(available) if rng.random() < epsilon else max(available, key=lambda a: values[a])
        next_state = _global_action_candidate(action)
        next_mask = visited_mask | _candidate_mask(next_state)
        success = _within_target_range(measured[next_state], low, high)
        reward = abs(math.log(measured[state] / target)) - abs(math.log(measured[next_state] / target)) - 0.02
        if success:
            reward += 1.0
        next_key = _global_policy_state(next_state, measured, target, next_mask)
        future_actions = _available_global_actions(next_mask)
        future = 0.0 if success or not future_actions else max(
            q.setdefault(next_key, [0.0] * len(GLOBAL_ACTIONS))[a] for a in future_actions
        )
        values[action] += alpha * (reward + gamma * future - values[action])
        state, visited_mask = next_state, next_mask
        if success:
            break


def _task_center(task: dict) -> float:
    if "target_hz" in task:
        return float(task["target_hz"])
    low, high = task["cutoff_range_hz"]
    return math.exp((math.log(low) + math.log(high)) / 2.0)


def _acceptance_range(task: dict) -> tuple[float, float]:
    if "cutoff_range_hz" in task:
        low, high = task["cutoff_range_hz"]
        if not (math.isfinite(low) and math.isfinite(high) and 0 < low <= high):
            raise ValueError(f"invalid held-out cutoff range in task {task.get('task_id')}")
        return float(low), float(high)
    target = float(task["target_hz"])
    tolerance = float(task["tolerance_percent"]) / 100.0
    if not (math.isfinite(target) and target > 0 and math.isfinite(tolerance) and tolerance > 0):
        raise ValueError(f"invalid training target in task {task.get('task_id')}")
    return target * (1.0 - tolerance), target * (1.0 + tolerance)


def _candidate_order(target_hz: float) -> list[tuple[int, int]]:
    return sorted(
        ((ri, ci) for ri in range(3) for ci in range(3)),
        key=lambda state: (
            abs(math.log((1.0 / (2.0 * math.pi * R_VALUES_OHM[state[0]] * C_VALUES_F[state[1]])) / target_hz)),
            R_VALUES_OHM[state[0]], C_VALUES_F[state[1]],
        ),
    )


def _evaluate_rl_episode(
    task: dict, start: tuple[int, int], measured: dict[tuple[int, int], float],
    q: dict[tuple[int, int, int], list[float]], budget: int,
) -> dict:
    low, high = _acceptance_range(task)
    target = _task_center(task)
    state = start
    initial_value = measured[state]
    attempts = [{
        "step": 0, "action": "START", "state": list(state),
        "resistance_ohm": R_VALUES_OHM[state[0]], "capacitance_f": C_VALUES_F[state[1]],
        "measured_cutoff_hz": initial_value, "reward": 0.0,
        "measured_pass": _within_target_range(initial_value, low, high),
    }]
    stop_reason = "measured_pass_at_start" if attempts[0]["measured_pass"] else "budget_exhausted"
    total_reward = 0.0
    while not attempts[-1]["measured_pass"] and len(attempts) < budget:
        action = _argmax(q[_policy_state(state, measured, target)])
        next_state = _transition(state, action)
        value = measured[next_state]
        reward = abs(math.log(measured[state] / target)) - abs(math.log(value / target)) - 0.02
        passed = _within_target_range(value, low, high)
        if passed:
            reward += 1.0
        total_reward += reward
        state = next_state
        attempts.append({
            "step": len(attempts), "action": ACTIONS[action], "state": list(state),
            "resistance_ohm": R_VALUES_OHM[state[0]], "capacitance_f": C_VALUES_F[state[1]],
            "measured_cutoff_hz": value, "reward": reward, "measured_pass": passed,
        })
        stop_reason = "measured_pass" if passed else "budget_exhausted"
    return _episode_record(task, start, attempts, budget, target, low, high, stop_reason, total_reward)


def _evaluate_global_rl_episode(
    task: dict,
    start: tuple[int, int],
    measured: dict[tuple[int, int], float],
    q: dict[tuple[int, int, int, int], list[float]],
    budget: int,
) -> dict:
    low, high = _acceptance_range(task)
    target = _task_center(task)
    state = start
    visited_mask = _candidate_mask(state)
    initial_value = measured[state]
    attempts = [{
        "step": 0, "action": "START", "state": list(state),
        "resistance_ohm": R_VALUES_OHM[state[0]], "capacitance_f": C_VALUES_F[state[1]],
        "measured_cutoff_hz": initial_value, "reward": 0.0,
        "measured_pass": _within_target_range(initial_value, low, high),
    }]
    stop_reason = "measured_pass_at_start" if attempts[0]["measured_pass"] else "budget_exhausted"
    total_reward = 0.0
    while not attempts[-1]["measured_pass"] and len(attempts) < budget:
        available = _available_global_actions(visited_mask)
        if not available:
            stop_reason = "candidate_space_exhausted"
            break
        key = _global_policy_state(state, measured, target, visited_mask)
        values = q.get(key, [0.0] * len(GLOBAL_ACTIONS))
        action = max(available, key=lambda index: values[index])
        next_state = _global_action_candidate(action)
        value = measured[next_state]
        reward = abs(math.log(measured[state] / target)) - abs(math.log(value / target)) - 0.02
        passed = _within_target_range(value, low, high)
        if passed:
            reward += 1.0
        total_reward += reward
        visited_mask |= _candidate_mask(next_state)
        state = next_state
        attempts.append({
            "step": len(attempts), "action": GLOBAL_ACTIONS[action], "state": list(state),
            "resistance_ohm": R_VALUES_OHM[state[0]], "capacitance_f": C_VALUES_F[state[1]],
            "measured_cutoff_hz": value, "reward": reward, "measured_pass": passed,
        })
        stop_reason = "measured_pass" if passed else "budget_exhausted"
    episode = _episode_record(task, start, attempts, budget, target, low, high, stop_reason, total_reward)
    episode["proposal_mode"] = "global"
    return episode


def _evaluate_conventional_episode(
    task: dict, start: tuple[int, int], measured: dict[tuple[int, int], float], budget: int,
) -> dict:
    low, high = _acceptance_range(task)
    target = _task_center(task)
    attempts = []
    total_reward = 0.0
    stop_reason = "budget_exhausted"
    for rank, state in enumerate(_candidate_order(target)[:budget], start=1):
        value = measured[state]
        passed = _within_target_range(value, low, high)
        reward = 1.0 if passed else -abs(math.log(value / target))
        total_reward += reward
        attempts.append({
            "step": rank, "action": f"ANALYTICAL_RANK_{rank}", "state": list(state),
            "resistance_ohm": R_VALUES_OHM[state[0]], "capacitance_f": C_VALUES_F[state[1]],
            "analytical_cutoff_hz": 1.0 / (2.0 * math.pi * R_VALUES_OHM[state[0]] * C_VALUES_F[state[1]]),
            "measured_cutoff_hz": value, "reward": reward, "measured_pass": passed,
        })
        if passed:
            stop_reason = "measured_pass"
            break
    return _episode_record(task, start, attempts, budget, target, low, high, stop_reason, total_reward)


def _evaluate_start_aware_conventional_episode(
    task: dict, start: tuple[int, int], measured: dict[tuple[int, int], float], budget: int,
) -> dict:
    """Rank globally but charge the same initial candidate measurement as RL."""
    low, high = _acceptance_range(task)
    target = _task_center(task)
    initial_value = measured[start]
    initial_pass = _within_target_range(initial_value, low, high)
    initial_reward = 1.0 if initial_pass else -abs(math.log(initial_value / target))
    attempts = [{
        "step": 0, "action": "START", "state": list(start),
        "resistance_ohm": R_VALUES_OHM[start[0]], "capacitance_f": C_VALUES_F[start[1]],
        "analytical_cutoff_hz": 1.0 / (2.0 * math.pi * R_VALUES_OHM[start[0]] * C_VALUES_F[start[1]]),
        "measured_cutoff_hz": initial_value, "reward": initial_reward, "measured_pass": initial_pass,
    }]
    total_reward = initial_reward
    stop_reason = "measured_pass_at_start" if initial_pass else "budget_exhausted"
    if not initial_pass:
        for rank, state in enumerate((state for state in _candidate_order(target) if state != start), start=1):
            if len(attempts) >= budget:
                break
            value = measured[state]
            passed = _within_target_range(value, low, high)
            reward = 1.0 if passed else -abs(math.log(value / target))
            total_reward += reward
            attempts.append({
                "step": len(attempts), "action": f"GLOBAL_RANK_{rank}", "state": list(state),
                "resistance_ohm": R_VALUES_OHM[state[0]], "capacitance_f": C_VALUES_F[state[1]],
                "analytical_cutoff_hz": 1.0 / (2.0 * math.pi * R_VALUES_OHM[state[0]] * C_VALUES_F[state[1]]),
                "measured_cutoff_hz": value, "reward": reward, "measured_pass": passed,
            })
            if passed:
                stop_reason = "measured_pass"
                break
    episode = _episode_record(task, start, attempts, budget, target, low, high, stop_reason, total_reward)
    episode["proposal_mode"] = "start_aware_global_ranking"
    return episode


def _episode_record(task: dict, start: tuple[int, int], attempts: list[dict], budget: int,
                    target: float, low: float, high: float, stop_reason: str, reward: float) -> dict:
    return {
        "task_id": task["task_id"], "target_hz": target, "target_range_hz": [low, high],
        "start_state": list(start), "attempts": attempts, "budget": budget,
        "budget_used": len(attempts), "success": bool(attempts and attempts[-1]["measured_pass"]),
        "total_reward": reward, "stop_reason": stop_reason,
    }


def _summary(episodes: list[dict]) -> dict:
    total = len(episodes)
    successes = sum(bool(episode["success"]) for episode in episodes)
    return {
        "episodes": total,
        "measured_passes": successes,
        "measured_pass_rate": successes / total if total else 0.0,
        "mean_candidate_measurements": sum(episode["budget_used"] for episode in episodes) / total if total else 0.0,
        "mean_reward": sum(episode["total_reward"] for episode in episodes) / total if total else 0.0,
    }


def run_benchmark(
    output_dir: str | Path,
    *,
    dataset_path: str | Path = DEFAULT_DATASET,
    seed: int = 20261025,
    training_episodes: int = 12000,
    candidate_budget: int = 3,
    training_max_steps: int = 8,
    proposal_mode: str = "local",
) -> dict:
    """Train and evaluate only against the saved nine-point measured dataset."""
    if isinstance(candidate_budget, bool) or candidate_budget < 1:
        raise ValueError("candidate_budget must be a positive integer")
    if proposal_mode not in {"local", "global"}:
        raise ValueError("proposal_mode must be 'local' or 'global'")
    output = Path(output_dir).expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing RL benchmark output: {output}")
    measured, provenance = load_measured_grid(dataset_path)
    training_tasks = generate_training_tasks(measured, training_episodes, seed)
    guard = TRAINING_GUARD_FRACTION
    for task in training_tasks:
        if any(
            heldout["cutoff_range_hz"][0] / (1.0 + guard)
            <= task["target_hz"]
            <= heldout["cutoff_range_hz"][1] * (1.0 + guard)
            for heldout in HELD_OUT_TASKS
        ):
            raise ValueError("training target manifest overlaps a guarded held-out range")

    if proposal_mode == "global":
        q = _train_global_q_policy(measured, seed, training_episodes, training_max_steps, training_tasks)
    else:
        q = _train_q_policy(
            measured, seed, training_episodes, training_max_steps, training_tasks=training_tasks,
        )
    starts = tuple(sorted(measured))
    if proposal_mode == "global":
        rl_episodes = [
            _evaluate_global_rl_episode(task, start, measured, q, candidate_budget)
            for task in HELD_OUT_TASKS for start in starts
        ]
        action_names = GLOBAL_ACTIONS
    else:
        rl_episodes = [
            _evaluate_rl_episode(task, start, measured, q, candidate_budget)
            for task in HELD_OUT_TASKS for start in starts
        ]
        action_names = ACTIONS
    conventional_episodes = [
        _evaluate_conventional_episode(task, start, measured, candidate_budget)
        for task in HELD_OUT_TASKS for start in starts
    ]
    start_aware_episodes = [
        _evaluate_start_aware_conventional_episode(task, start, measured, candidate_budget)
        for task in HELD_OUT_TASKS for start in starts
    ]
    rl_summary = _summary(rl_episodes)
    conventional_summary = _summary(conventional_episodes)
    start_aware_summary = _summary(start_aware_episodes)
    rate_delta = rl_summary["measured_pass_rate"] - conventional_summary["measured_pass_rate"]
    if rate_delta > 0:
        interpretation = "RL achieved a higher measured-pass rate under this bounded benchmark; this is narrow experimental evidence only."
    elif rate_delta < 0:
        interpretation = "The conventional analytical ranking achieved a higher measured-pass rate under this bounded benchmark; RL did not beat the baseline."
    else:
        interpretation = "RL and conventional ranking had the same measured-pass rate under this bounded benchmark; no RL advantage was shown."

    training_config = {
        "algorithm": "tabular Q-learning with epsilon-greedy exploration",
        "seed": seed,
        "training_episodes": training_episodes,
        "training_max_steps": training_max_steps,
        "proposal_mode": proposal_mode,
        "training_target_distribution": "seeded log-uniform over the measured cutoff span excluding held-out ranges expanded by a multiplicative guard fraction",
        "training_target_guard_fraction": TRAINING_GUARD_FRACTION,
        "training_tolerance_percent": TRAINING_TOLERANCE_PERCENT,
        "training_target_generation_seed_salt": _TRAINING_TARGET_SEED_SALT,
        "training_allowed_target_intervals_hz": [list(interval) for interval in _training_target_intervals(measured)],
        "alpha": 0.25, "gamma": 0.9, "epsilon_start": 1.0, "epsilon_min": 0.05,
        "epsilon_decay": 0.997,
        "state": "R index, C index, and target-relative measured-cutoff error band"
                  + (" plus visited-grid bitmask" if proposal_mode == "global" else ""),
        "actions": list(action_names),
        "reward": "absolute log-error improvement - 0.02 per measured-table step +1.0 on training target within 1%",
        "evaluation_candidate_measurement_budget_per_episode": candidate_budget,
        "acceptance_evidence": "saved LTspice measured_cutoff_hz only; analytical cutoff is used only for conventional ranking",
    }

    def greedy_action_name(key: tuple[int, ...], values: list[float]) -> str:
        allowed = _available_global_actions(key[3]) if proposal_mode == "global" else list(range(len(action_names)))
        return action_names[max(allowed, key=lambda index: values[index])] if allowed else "NO_UNVISITED_CANDIDATE"

    policy = {
        "format": f"circuitpilot-target-aware-rc-q-policy-{proposal_mode}-v1",
        "action_values": {
            ",".join(str(part) for part in key): values
            for key, values in sorted(q.items())
        },
        "greedy_actions": {
            ",".join(str(part) for part in key): greedy_action_name(key, values)
            for key, values in sorted(q.items())
        },
    }
    tasks = {
        "training_tasks": training_tasks,
        "training_task_manifest_sha256": hashlib.sha256(
            json.dumps(training_tasks, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "training_target_distribution": training_config["training_target_distribution"],
        "training_target_guard_fraction": TRAINING_GUARD_FRACTION,
        "held_out_tasks": [dict(task) for task in HELD_OUT_TASKS],
        "held_out_targets_disjoint_from_training": True,
        "start_states_per_task": [list(state) for state in starts],
    }
    episode_history = {
        "rl_policy": rl_episodes,
        "conventional_analytical_ranking": conventional_episodes,
        "start_aware_conventional_global_ranking": start_aware_episodes,
    }
    summary = {
        "benchmark_id": "rc_lowpass_target_aware_rl_vs_ranking_v2",
        "policy_status": "experimental_not_integrated",
        "production_qualified": False,
        "proposal_mode": proposal_mode,
        "candidate_budget_per_episode": candidate_budget,
        "rl": rl_summary,
        "conventional_analytical_ranking": conventional_summary,
        "start_aware_conventional_global_ranking": start_aware_summary,
        "rl_minus_conventional_measured_pass_rate": rate_delta,
        "interpretation": interpretation,
        "limitation": "One fixed nine-candidate ideal RC grid and five held-out ranges; results do not establish generalization to other grids, conditions, topologies, or LTspice re-simulation.",
    }
    benchmark = {
        "benchmark_id": summary["benchmark_id"],
        "seed": seed,
        "proposal_mode": proposal_mode,
        "dataset_provenance": provenance,
        "candidate_grid": {"resistance_values_ohm": list(R_VALUES_OHM), "capacitance_values_f": list(C_VALUES_F)},
        "training_config": training_config,
        "tasks": tasks,
        "summary": summary,
        "artifact_files": ["training-config.json", "tasks.json", "policy.json", "episodes.json", "summary.json"],
    }

    output.mkdir(parents=True, exist_ok=False)
    _write_json(output / "training-config.json", training_config)
    _write_json(output / "tasks.json", tasks)
    _write_json(output / "policy.json", policy)
    _write_json(output / "episodes.json", episode_history)
    _write_json(output / "summary.json", summary)
    _write_json(output / "benchmark.json", benchmark)
    return benchmark


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
