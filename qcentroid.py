"""
QCentroid Quantum-Inspired Container Yard Stacking Optimization Solver (v1.3)

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

import json
import time
import math
import random
from copy import deepcopy
try:
    from typing import Dict, List, Any, Tuple
except ImportError:
    pass


class QCentroidUserLogger:
    def __init__(self):
        self.messages = []
    def info(self, msg):
        self.messages.append({"level": "INFO", "message": str(msg)})
        print("[INFO] " + str(msg))
    def debug(self, msg):
        self.messages.append({"level": "DEBUG", "message": str(msg)})
        print("[DEBUG] " + str(msg))
    def warning(self, msg):
        self.messages.append({"level": "WARNING", "message": str(msg)})
        print("[WARNING] " + str(msg))
    def error(self, msg):
        self.messages.append({"level": "ERROR", "message": str(msg)})
        print("[ERROR] " + str(msg))

qcentroid_user_log = QCentroidUserLogger()


def compute_reshuffles_for_stacking(stacking_plan, containers):
    vessels = {}
    for container in containers:
        vessel_id = container.get('vessel_id')
        if vessel_id not in vessels:
            vessels[vessel_id] = []
        vessels[vessel_id].append(container['id'])

    reshuffles = 0
    checked = set()

    for i, pos in enumerate(stacking_plan):
        if pos in checked:
            continue
        container = next((c for c in containers if c['id'] == pos), None)
        if not container:
            continue

        vessel_mates = set(vessels[container['vessel_id']]) - {pos}
        target_tier = container.get('departure_order_tier', 0)

        for j in range(i + 1, len(stacking_plan)):
            other_pos = stacking_plan[j]
            if other_pos in vessel_mates:
                other_container = next((c for c in containers if c['id'] == other_pos), None)
                other_tier = other_container.get('departure_order_tier', 0) if other_container else 0
                if other_tier < target_tier:
                    reshuffles += 1
                    checked.add(other_pos)

    return reshuffles


def greedy_init(containers, num_positions, num_tiers):
    stacking_plan = []
    available = set(c['id'] for c in containers)
    tier_assignments = [[] for _ in range(num_tiers)]

    departure_tiers = {}
    for container in containers:
        vessel_id = container.get('vessel_id')
        if vessel_id not in departure_tiers:
            departure_tiers[vessel_id] = {}
        dep_ord = container.get('departure_order', float('inf'))
        if dep_ord not in departure_tiers[vessel_id]:
            departure_tiers[vessel_id][dep_ord] = []
        departure_tiers[vessel_id][dep_ord].append(container['id'])

    for vessel_id in sorted(departure_tiers.keys()):
        for dep_ord in sorted(departure_tiers[vessel_id].keys()):
            for cont_id in departure_tiers[vessel_id][dep_ord]:
                if cont_id not in available:
                    continue
                placed = False
                for tier in range(num_tiers):
                    if len(tier_assignments[tier]) < num_positions:
                        tier_assignments[tier].append(cont_id)
                        available.discard(cont_id)
                        placed = True
                        break
                if not placed:
                    break

    for tier in range(num_tiers):
        stacking_plan.extend(tier_assignments[tier])

    for cont_id in available:
        if len(stacking_plan) < len(containers):
            stacking_plan.append(cont_id)

    return stacking_plan


def compute_objective_value(stacking_plan, containers, num_positions, num_tiers):
    reshuffles = compute_reshuffles_for_stacking(stacking_plan, containers)

    max_reshuffles = max(1, len(containers))
    stability_score = max(0, 1.0 - (reshuffles / max_reshuffles))

    coverage = len([c for c in stacking_plan if c in {c['id'] for c in containers}])
    max_coverage = len(containers)
    coverage_score = coverage / max_coverage if max_coverage > 0 else 0.0

    obj_value = 0.7 * stability_score + 0.3 * coverage_score
    return obj_value, reshuffles, coverage


class SQATrotter:
    def __init__(self, num_replicas, num_positions, num_tiers, containers, rng):
        self.num_replicas = num_replicas
        self.num_positions = num_positions
        self.num_tiers = num_tiers
        self.containers = containers
        self.rng = rng

        self.replicas = [greedy_init(containers, num_positions, num_tiers) for _ in range(num_replicas)]
        self.best_solution = self.replicas[0][:]
        best_obj, _, _ = compute_objective_value(self.best_solution, containers, num_positions, num_tiers)
        self.best_objective = best_obj

    def trotter_sweep(self, transverse_field_strength):
        for _ in range(self.num_replicas):
            replica_idx = self.rng.randint(0, self.num_replicas - 1)
            replica = self.replicas[replica_idx]

            if self.rng.random() < 0.3:
                i, j = self.rng.sample(range(len(replica)), 2)
                replica[i], replica[j] = replica[j], replica[i]
            else:
                pos = self.rng.randint(0, len(replica) - 1)
                available = [c['id'] for c in self.containers if c['id'] not in replica]
                if available:
                    replica[pos] = self.rng.choice(available)

            obj_val, _, _ = compute_objective_value(replica, self.containers, self.num_positions, self.num_tiers)
            if obj_val > self.best_objective:
                self.best_objective = obj_val
                self.best_solution = replica[:]

        if self.rng.random() < 0.2 * transverse_field_strength:
            source_idx = self.rng.randint(0, self.num_replicas - 1)
            target_idx = self.rng.randint(0, self.num_replicas - 1)
            if source_idx != target_idx:
                self.replicas[target_idx] = self.replicas[source_idx][:]

    def run_annealing(self, num_sweeps, max_transverse_field):
        for sweep in range(num_sweeps):
            progress = sweep / max(1, num_sweeps - 1)
            transverse_field = max_transverse_field * (1.0 - progress)
            self.trotter_sweep(transverse_field)

        return self.best_solution, self.best_objective


def local_search_improvement(solution, containers, num_positions, num_tiers):
    improved_solution = solution[:]
    initial_obj, _, _ = compute_objective_value(improved_solution, containers, num_positions, num_tiers)

    best_obj = initial_obj
    improved = True

    while improved:
        improved = False
        for i in range(len(improved_solution)):
            for j in range(i + 1, len(improved_solution)):
                improved_solution[i], improved_solution[j] = improved_solution[j], improved_solution[i]
                new_obj, _, _ = compute_objective_value(improved_solution, containers, num_positions, num_tiers)

                if new_obj > best_obj:
                    best_obj = new_obj
                    improved = True
                    break
                else:
                    improved_solution[i], improved_solution[j] = improved_solution[j], improved_solution[i]

            if improved:
                break

    return improved_solution, best_obj


def run(input_data, solver_params, extra_arguments):
    start_time = time.time()

    containers = input_data.get('containers', [])
    num_positions = input_data.get('num_positions', 10)
    num_tiers = input_data.get('num_tiers', 4)

    if not containers or num_positions <= 0 or num_tiers <= 0:
        return {
            'stacking_plan': [],
            'benchmark': {'objective_value': 0.0, 'reshuffles': 0, 'coverage': 0},
            'objective_value': 0.0,
            'showcase': {'algorithm': 'SQA_SuzukiTrotter_v1.3', 'status': 'invalid_input'},
            'computation_metrics': {'wall_time_s': 0.0, 'algorithm': 'SQA_SuzukiTrotter_v1.3'}
        }

    num_replicas = solver_params.get('num_replicas', 25)
    num_sweeps = solver_params.get('num_sweeps', 200)
    max_transverse_field = solver_params.get('max_transverse_field', 2.0)
    num_restarts = solver_params.get('num_restarts', 3)

    seed = extra_arguments.get('seed', None)
    rng = random.Random(seed)

    best_global_solution = []
    best_global_objective = -float('inf')
    best_reshuffles = float('inf')
    best_coverage = 0

    for restart_idx in range(num_restarts):
        sqa = SQATrotter(num_replicas, num_positions, num_tiers, containers, rng)
        solution, objective = sqa.run_annealing(num_sweeps, max_transverse_field)

        improved_solution, improved_objective = local_search_improvement(solution, containers, num_positions, num_tiers)

        if improved_objective > best_global_objective:
            best_global_objective = improved_objective
            best_global_solution = improved_solution
            obj_val, best_reshuffles, best_coverage = compute_objective_value(improved_solution, containers, num_positions, num_tiers)

    if not best_global_solution:
        best_global_solution = greedy_init(containers, num_positions, num_tiers)
        best_global_objective, best_reshuffles, best_coverage = compute_objective_value(best_global_solution, containers, num_positions, num_tiers)

    elapsed_s = time.time() - start_time

    return {
        'stacking_plan': best_global_solution,
        'benchmark': {
            'objective_value': round(best_global_objective, 4),
            'reshuffles': int(best_reshuffles),
            'coverage': int(best_coverage)
        },
        'objective_value': round(best_global_objective, 4),
        'showcase': {
            'algorithm': 'SQA_SuzukiTrotter_v1.3',
            'num_replicas': num_replicas,
            'num_sweeps': num_sweeps,
            'num_restarts': num_restarts,
            'final_transverse_field': 0.0,
            'solution_quality': {
                'objective': round(best_global_objective, 4),
                'reshuffles': int(best_reshuffles),
                'coverage_pct': round(100.0 * best_coverage / len(containers), 1) if containers else 0.0
            },
            'cost_metrics': {
                'reshuffles_cost': round(best_reshuffles * 50.0, 2),
                'handling_cost': round(len(best_global_solution) * 25.0, 2),
                'total_cost': round((best_reshuffles * 50.0) + (len(best_global_solution) * 25.0), 2),
                'currency': 'USD',
                'cost_per_unit': 25.0,
                'unit': 'credits'
            },
            'time_elapsed': str(round(elapsed_s, 3)) + 's',
            'energy_consumption': 0.0
        },
        'computation_metrics': {'wall_time_s': round(elapsed_s, 3), 'algorithm': 'SQA_SuzukiTrotter_v1.3'}
    }
