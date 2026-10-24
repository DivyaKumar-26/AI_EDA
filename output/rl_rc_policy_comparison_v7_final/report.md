# Local versus global RC proposals

- Local policy: 14/45 measured passes.
- Global policy: 18/45 measured passes.
- Historical analytical ranking (start uncharged): 45/45.
- Start-aware analytical ranking: 45/45.
- Theoretical local/global pass ceilings under budget 3: 33/45 and 45/45.

All methods use the same measured candidate table. Local RL can only move to adjacent grid states. Global RL and both ranking baselines can select any unvisited grid candidate directly after the start measurement; the historical baseline does not charge that start measurement, while the start-aware baseline does.

Global RL masks all previously measured candidates during training and evaluation. All acceptance checks use the existing saved measured cutoffs. This is an offline fixed-grid benchmark, not a simulator or production qualification result.

See comparison.json for task/start traces and miss classifications.
