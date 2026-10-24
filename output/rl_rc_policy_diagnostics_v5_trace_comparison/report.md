# RC policy training comparison

- Prior v4: 20/45 passes (44.4%).
- New v5: 14/45 passes (31.1%).
- Conventional analytical ranking: 45/45 passes (100.0%).
- Structural RL ceiling at budget 3: 33/45 (73.3%).

An equal candidate-measurement budget does not give the methods the same candidate access. RL starts at the supplied grid state and can make only adjacent R/C moves; the budget of three includes the start measurement, leaving two moves. Analytical ranking can jump directly to any grid candidate, so its 45/45 result is not bounded by RL's local-move reachability ceiling.

The miss-category counts separate tasks with no passing grid point, structurally unreachable passing points under the local budget, repeated/stalled trajectories, and avoidable misses where a passing point was reachable. See `comparison.json` for every task/start trace.

This is a fixed-grid offline diagnostic using the existing saved LTspice measurements. No new simulations were run; it is not production qualification.
