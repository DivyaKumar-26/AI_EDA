"""Dependency-free tabular RL environment for measured RC cutoff tuning."""

from __future__ import annotations

from dataclasses import dataclass
import math
import random
from typing import Mapping


Action = int  # 0: R up, 1: R down, 2: C up, 3: C down


@dataclass(frozen=True)
class RCState:
    resistance_index: int
    capacitance_index: int


class RCLowpassTuningEnv:
    """A small measured-state environment; actions move one component value by one grid step."""

    ACTIONS = ("R_UP", "R_DOWN", "C_UP", "C_DOWN")

    def __init__(
        self,
        resistance_values: tuple[float, ...],
        capacitance_values: tuple[float, ...],
        measured_cutoffs_hz: Mapping[RCState, float],
        target_cutoff_hz: float,
        tolerance_percent: float = 1.0,
        max_steps: int = 8,
        seed: int = 0,
    ) -> None:
        if len(resistance_values) < 2 or len(capacitance_values) < 2:
            raise ValueError("R and C grids each require at least two values")
        if any(not math.isfinite(v) or v <= 0 for v in (*resistance_values, *capacitance_values)):
            raise ValueError("component grid values must be finite and positive")
        if any(a >= b for a, b in zip(resistance_values, resistance_values[1:])) or any(a >= b for a, b in zip(capacitance_values, capacitance_values[1:])):
            raise ValueError("component grid values must be strictly increasing")
        if not math.isfinite(target_cutoff_hz) or target_cutoff_hz <= 0:
            raise ValueError("target cutoff must be finite and positive")
        if not math.isfinite(tolerance_percent) or tolerance_percent <= 0:
            raise ValueError("tolerance_percent must be finite and positive")
        if max_steps < 1:
            raise ValueError("max_steps must be positive")
        self.resistance_values = tuple(resistance_values)
        self.capacitance_values = tuple(capacitance_values)
        self.measured_cutoffs_hz = dict(measured_cutoffs_hz)
        expected = {RCState(i, j) for i in range(len(resistance_values)) for j in range(len(capacitance_values))}
        if set(self.measured_cutoffs_hz) != expected:
            raise ValueError("measured cutoff table must contain exactly one result for every grid state")
        if any(not math.isfinite(value) or value <= 0 for value in self.measured_cutoffs_hz.values()):
            raise ValueError("measured cutoff values must be finite and positive")
        self.target_cutoff_hz = target_cutoff_hz
        self.tolerance_percent = tolerance_percent
        self.max_steps = max_steps
        self._rng = random.Random(seed)
        self.state: RCState | None = None
        self.steps = 0

    def reset(self, start_state: RCState | None = None) -> RCState:
        if start_state is None:
            start_state = RCState(self._rng.randrange(len(self.resistance_values)), self._rng.randrange(len(self.capacitance_values)))
        if start_state not in self.measured_cutoffs_hz:
            raise ValueError("start_state is outside the measured component grid")
        self.state = start_state
        self.steps = 0
        return start_state

    def step(self, action: Action) -> tuple[RCState, float, bool, dict]:
        if self.state is None:
            raise RuntimeError("call reset before step")
        if action not in range(len(self.ACTIONS)):
            raise ValueError(f"action must be an integer in 0..{len(self.ACTIONS)-1}")
        previous = self.state
        candidate = self._transition(previous, action)
        self.steps += 1
        self.state = candidate
        previous_error = self._log_error(previous)
        error = self._log_error(candidate)
        success = self._within_tolerance(candidate)
        reward = previous_error - error - 0.02  # improvement shaping and simulation cost
        if candidate == previous:
            reward -= 0.1  # invalid boundary move
        if success:
            reward += 1.0
        done = success or self.steps >= self.max_steps
        return candidate, reward, done, {
            "measured_cutoff_hz": self.measured_cutoffs_hz[candidate],
            "relative_error_percent": self._error_percent(candidate),
            "success": success,
            "action": self.ACTIONS[action],
            "step": self.steps,
        }

    def _error_percent(self, state: RCState) -> float:
        measured = self.measured_cutoffs_hz[state]
        return 100.0 * abs(measured - self.target_cutoff_hz) / self.target_cutoff_hz

    def _log_error(self, state: RCState) -> float:
        return abs(math.log(self.measured_cutoffs_hz[state] / self.target_cutoff_hz))

    def _within_tolerance(self, state: RCState) -> bool:
        return self._error_percent(state) <= self.tolerance_percent

    def _transition(self, state: RCState, action: Action) -> RCState:
        r, c = state.resistance_index, state.capacitance_index
        if action == 0:
            r += 1
        elif action == 1:
            r -= 1
        elif action == 2:
            c += 1
        else:
            c -= 1
        if not (0 <= r < len(self.resistance_values) and 0 <= c < len(self.capacitance_values)):
            return state
        return RCState(r, c)


def train_q_learning(
    env: RCLowpassTuningEnv,
    episodes: int = 3000,
    seed: int = 0,
    alpha: float = 0.25,
    gamma: float = 0.95,
    epsilon_start: float = 1.0,
    epsilon_min: float = 0.04,
    epsilon_decay: float = 0.997,
) -> dict[RCState, list[float]]:
    """Train a tabular Q policy over the small bounded component lattice."""
    if episodes < 1 or not 0 < alpha <= 1 or not 0 <= gamma < 1:
        raise ValueError("invalid Q-learning training configuration")
    rng = random.Random(seed)
    q = {state: [0.0] * len(env.ACTIONS) for state in env.measured_cutoffs_hz}
    epsilon = epsilon_start
    states = tuple(q)
    for _ in range(episodes):
        state = env.reset(rng.choice(states))
        if env._within_tolerance(state):
            continue
        for _ in range(env.max_steps):
            if rng.random() < epsilon:
                action = rng.randrange(len(env.ACTIONS))
            else:
                action = _argmax_random_tie(q[state], rng)
            next_state, reward, done, _ = env.step(action)
            future = 0.0 if done else max(q[next_state])
            q[state][action] += alpha * (reward + gamma * future - q[state][action])
            state = next_state
            if done:
                break
        epsilon = max(epsilon_min, epsilon * epsilon_decay)
    return q


def _argmax_random_tie(values: list[float], rng: random.Random) -> int:
    maximum = max(values)
    choices = [index for index, value in enumerate(values) if value == maximum]
    return rng.choice(choices)


def evaluate_greedy_policy(env: RCLowpassTuningEnv, q: dict[RCState, list[float]]) -> dict:
    """Evaluate greedily from every measured component state."""
    episodes = []
    for start in env.measured_cutoffs_hz:
        state = env.reset(start)
        if env._within_tolerance(state):
            episodes.append({"start": [start.resistance_index, start.capacitance_index], "success": True, "steps": 0,
                             "final_error_percent": env._error_percent(state)})
            continue
        done = False
        for _ in range(env.max_steps):
            action = max(range(len(env.ACTIONS)), key=lambda index: q[state][index])
            state, _, done, info = env.step(action)
            if done:
                break
        episodes.append({"start": [start.resistance_index, start.capacitance_index], "success": bool(info["success"]),
                         "steps": env.steps, "final_error_percent": env._error_percent(state)})
    successes = sum(episode["success"] for episode in episodes)
    return {"episodes": episodes, "successes": successes, "total": len(episodes),
            "success_rate": successes / len(episodes),
            "mean_steps": sum(episode["steps"] for episode in episodes) / len(episodes)}


def evaluate_random_policy(env: RCLowpassTuningEnv, repetitions: int = 50, seed: int = 1) -> dict:
    rng = random.Random(seed)
    episodes = []
    for start in env.measured_cutoffs_hz:
        for _ in range(repetitions):
            state = env.reset(start)
            done = env._within_tolerance(state)
            info = {"success": done}
            while not done and env.steps < env.max_steps:
                state, _, done, info = env.step(rng.randrange(len(env.ACTIONS)))
            episodes.append({"success": bool(info["success"]), "steps": env.steps})
    successes = sum(episode["success"] for episode in episodes)
    return {"episodes": len(episodes), "successes": successes, "success_rate": successes / len(episodes),
            "mean_steps": sum(episode["steps"] for episode in episodes) / len(episodes)}
