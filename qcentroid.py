"""QCentroid Quantum-Inspired Container Yard Stacking Optimization Solver (v1.3)

Simulated Quantum Annealing (SQA) using Suzuki-Trotter decomposition:
  - Multiple Trotter replicas hold parallel stacking plans
  - Transverse field coupling enables quantum tunneling between replicas
  - Path-integral Monte Carlo updates explore solution space
  - Annealing schedule reduces transverse field, freezing into good solutions

v1.3 Upgrade (from v1.2):
  - Multi-restart SQA: 3 independent runs with different seeds, pick best
  - Post-SQA local search: exhaustive pairwise swap improvement pass
  - Departure-order tier optimization in greedy init
  - Enhanced output with richer showcase data

v1.2 Features (retained):
  - Vessel-aware smart moves: relocate toward vessel-mates, swap misplaced
  - Adaptive move selection based on annealing progress
  - 25 Trotter slices, high transverse field range

Based on: Kadowaki & Nishimori (1998), Martonak et al. (2002),
Das & Chakrabarti (2008).

Entry point: run(input_data, solver_params, extra_arguments) -> dict
"""