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
        vid = container['vessel_id']
        if vid not in vessels:
            vessels[vid] = {'departure_order': container['vessel_departure_order'], 'containers': []}
        vessels[vid]['containers'].append(container)
    sorted_vessels = sorted(vessels.items(), key=lambda x: x[1]['departure_order'])
    stack_location = {}
    for a in stacking_plan:
        stack_location[a['id']] = {'block': a['assigned_block'], 'row': a['assigned_row'], 'bay': a['assigned_bay'], 'tier': a['tier_level']}
    total_reshuffles = 0
    reshuffles_per_vessel = {}
    globally_removed = set()
    for vessel_id, vessel_info in sorted_vessels:
        vessel_containers_sorted = sorted(vessel_info['containers'], key=lambda c: c['priority'])
        vessel_reshuffles = 0
        for target in vessel_containers_sorted:
            target_id = target['id']
            if target_id not in stack_location:
                continue
            loc = stack_location[target_id]
            containers_above = 0
            for other in stacking_plan:
                if (other['assigned_block'] == loc['block'] and other['assigned_row'] == loc['row'] and other['assigned_bay'] == loc['bay'] and other['tier_level'] > loc['tier'] and other['id'] not in globally_removed):
                    containers_above += 1
            vessel_reshuffles += containers_above
            globally_removed.add(target_id)
        total_reshuffles += vessel_reshuffles
        reshuffles_per_vessel[vessel_id] = vessel_reshuffles
    return total_reshuffles, reshuffles_per_vessel


def compute_vessel_grouping_score(stacking_plan, containers):
    vessel_assignments = {}
    for a in stacking_plan:
        cid = a['id']
        vid = None
        for c in containers:
            if c['id'] == cid:
                vid = c['vessel_id']
                break
        if vid not in vessel_assignments:
            vessel_assignments[vid] = []
        vessel_assignments[vid].append((a['assigned_block'], a['assigned_row'], a['assigned_bay']))
    total_distance = 0
    total_pairs = 0
    for vid, locations in vessel_assignments.items():
        if len(locations) <= 1:
            continue
        for i in range(len(locations)):
            for j in range(i + 1, len(locations)):
                l1, l2 = locations[i], locations[j]
                dist = (0 if l1[0] == l2[0] else 3) + abs(l1[1] - l2[1]) + abs(l1[2] - l2[2])
                total_distance += dist
        total_pairs += len(locations) * (len(locations) - 1) / 2
    if total_pairs == 0:
        return 1.0
    avg_distance = total_distance / total_pairs
    return max(0, 1.0 - (avg_distance / 20.0))


def compute_block_utilization(stacking_plan, yard_layout):
    block_counts = {}
    for a in stacking_plan:
        bid = a['assigned_block']
        block_counts[bid] = block_counts.get(bid, 0) + 1
    utilization = {}
    for block in yard_layout['blocks']:
        bid = block['block_id']
        cap = block['total_capacity']
        utilization[bid] = block_counts.get(bid, 0) / cap if cap > 0 else 0.0
    return utilization


def compute_weight_balance_score(stacking_plan, container_map, yard_layout):
    block_weights = {}
    for block in yard_layout['blocks']:
        block_weights[block['block_id']] = 0.0
    for a in stacking_plan:
        block_weights[a['assigned_block']] += container_map[a['id']]['weight_tonnes']
    weights = list(block_weights.values())
    if not weights or sum(weights) == 0:
        return 1.0
    mean_w = sum(weights) / len(weights)
    if mean_w == 0:
        return 1.0
    variance = sum((w - mean_w) ** 2 for w in weights) / len(weights)
    cv = math.sqrt(variance) / mean_w
    return max(0, 1.0 - cv)


def check_weight_stability(stacking_plan, container_map, yard_layout):
    stacks = {}
    for a in stacking_plan:
        key = (a['assigned_block'], a['assigned_row'], a['assigned_bay'])
        if key not in stacks:
            stacks[key] = []
        stacks[key].append((a['tier_level'], container_map[a['id']]['weight_tonnes']))
    for key, tier_list in stacks.items():
        tier_list.sort()
        for i in range(len(tier_list) - 1):
            if tier_list[i][1] < tier_list[i + 1][1]:
                return False
    return True


def estimate_reshuffles_single(container_id, stacking_plan):
    my_loc = None
    for a in stacking_plan:
        if a['id'] == container_id:
            my_loc = a
            break
    if my_loc is None:
        return 0
    count = 0
    for a in stacking_plan:
        if (a['assigned_block'] == my_loc['assigned_block'] and a['assigned_row'] == my_loc['assigned_row'] and a['assigned_bay'] == my_loc['assigned_bay'] and a['tier_level'] > my_loc['tier_level']):
            count += 1
    return count


def greedy_initial_stacking(containers, yard_layout, logger):
    logger.info("Starting vessel-aware greedy initialization with " + str(len(containers)) + " containers")
    vessel_groups = {}
    for container in containers:
        vessel_id = container['vessel_id']
        if vessel_id not in vessel_groups:
            vessel_groups[vessel_id] = []
        vessel_groups[vessel_id].append(container)
    sorted_vessels = sorted(vessel_groups.items(), key=lambda x: x[1][0]['vessel_departure_order'])
    stacking_plan = []
    container_map = {c['id']: c for c in containers}
    stack_usage = {}
    block_capacities = {}
    for block in yard_layout['blocks']:
        block_capacities[block['block_id']] = {'capacity': block['total_capacity'], 'used': 0}

    def find_preferred_block(vessel_id, yard_layout, block_capacities):
        blocks = yard_layout['blocks']
        available_blocks = []
        for block in blocks:
            block_id = block['block_id']
            current_util = block_capacities[block_id]['used'] / block_capacities[block_id]['capacity']
            available_capacity = block_capacities[block_id]['capacity'] - block_capacities[block_id]['used']
            if available_capacity > 0:
                available_blocks.append((block_id, current_util, available_capacity))
        if not available_blocks:
            return None
        available_blocks.sort(key=lambda x: x[1])
        return available_blocks[0][0]

    for vessel_id, vessel_containers in sorted_vessels:
        vessel_containers_sorted = sorted(vessel_containers, key=lambda c: -c['weight_tonnes'])
        preferred_block = find_preferred_block(vessel_id, yard_layout, block_capacities)
        if preferred_block is None:
            logger.warning("No available blocks for vessel " + str(vessel_id))
            continue
        for container in vessel_containers_sorted:
            cid = container['id']
            weight = container['weight_tonnes']
            placed = False
            blocks_to_try = [preferred_block]
            if block_capacities[preferred_block]['used'] >= block_capacities[preferred_block]['capacity']:
                blocks_to_try = [b['block_id'] for b in yard_layout['blocks'] if b['block_id'] != preferred_block]
            for block_id in blocks_to_try:
                if placed:
                    break
                block = None
                for b in yard_layout['blocks']:
                    if b['block_id'] == block_id:
                        block = b
                        break
                if block is None:
                    continue
                max_tier = block['max_tier_height']
                for row_idx in range(block['rows']):
                    if placed:
                        break
                    for bay_idx in range(block['bays_per_row']):
                        stack_key = (block_id, row_idx, bay_idx)
                        current_tier = stack_usage.get(stack_key, 0)
                        if current_tier < max_tier:
                            can_place = True
                            if current_tier > 0:
                                for existing in stacking_plan:
                                    if (existing['assigned_block'] == block_id and existing['assigned_row'] == row_idx and existing['assigned_bay'] == bay_idx and existing['tier_level'] == current_tier - 1):
                                        below_weight = container_map[existing['id']]['weight_tonnes']
                                        if below_weight < weight:
                                            can_place = False
                                        break
                            if can_place:
                                stacking_plan.append({'id': cid, 'assigned_block': block_id, 'assigned_row': row_idx, 'assigned_bay': bay_idx, 'tier_level': current_tier, 'reshuffles_if_retrieved_now': 0})
                                stack_usage[stack_key] = current_tier + 1
                                block_capacities[block_id]['used'] += 1
                                placed = True
                                break
            if not placed:
                logger.warning("Could not place container " + str(cid))
    logger.info("Greedy placement: " + str(len(stacking_plan)) + "/" + str(len(containers)) + " placed")
    return stacking_plan


def compute_objective(stacking_plan, containers, grouping_weight=0.5, balance_weight=0.3, yard_layout=None):
    total_reshuffles, _ = compute_reshuffles_for_stacking(stacking_plan, containers)
    grouping_score = compute_vessel_grouping_score(stacking_plan, containers)
    grouping_penalty = (1.0 - grouping_score) * grouping_weight * 100
    balance_penalty = 0.0
    if yard_layout and balance_weight > 0:
        container_map = {c['id']: c for c in containers}
        balance_score = compute_weight_balance_score(stacking_plan, container_map, yard_layout)
        balance_penalty = (1.0 - balance_score) * balance_weight * 100
    return total_reshuffles + grouping_penalty + balance_penalty


def sqa_swap_move(plan, container_map, yard_layout):
    if len(plan) < 2:
        return deepcopy(plan)
    new_plan = deepcopy(plan)
    i1, i2 = random.sample(range(len(new_plan)), 2)
    b1, r1, bay1, t1 = new_plan[i1]['assigned_block'], new_plan[i1]['assigned_row'], new_plan[i1]['assigned_bay'], new_plan[i1]['tier_level']
    new_plan[i1]['assigned_block'] = new_plan[i2]['assigned_block']
    new_plan[i1]['assigned_row'] = new_plan[i2]['assigned_row']
    new_plan[i1]['assigned_bay'] = new_plan[i2]['assigned_bay']
    new_plan[i1]['tier_level'] = new_plan[i2]['tier_level']
    new_plan[i2]['assigned_block'] = b1
    new_plan[i2]['assigned_row'] = r1
    new_plan[i2]['assigned_bay'] = bay1
    new_plan[i2]['tier_level'] = t1
    if not check_weight_stability(new_plan, container_map, yard_layout):
        return deepcopy(plan)
    return new_plan


def sqa_vessel_aware_swap(plan, container_map, yard_layout, vessel_block_map):
    """Swap two containers from different vessels that are in each other's preferred block."""
    if len(plan) < 2:
        return deepcopy(plan)
    new_plan = deepcopy(plan)
    # Find a container not in its vessel's preferred block
    misplaced = []
    for i, a in enumerate(new_plan):
        vid = container_map[a['id']]['vessel_id']
        preferred = vessel_block_map.get(vid)
        if preferred and a['assigned_block'] != preferred:
            misplaced.append(i)
    if len(misplaced) < 1:
        # Fall back to random swap
        return sqa_swap_move(plan, container_map, yard_layout)
    i1 = random.choice(misplaced)
    vid1 = container_map[new_plan[i1]['id']]['vessel_id']
    pref1 = vessel_block_map.get(vid1)
    # Find a container in pref1's block from a different vessel
    candidates = [i for i, a in enumerate(new_plan) if a['assigned_block'] == pref1 and container_map[a['id']]['vessel_id'] != vid1 and i != i1]
    if not candidates:
        return sqa_swap_move(plan, container_map, yard_layout)
    i2 = random.choice(candidates)
    b1, r1, bay1, t1 = new_plan[i1]['assigned_block'], new_plan[i1]['assigned_row'], new_plan[i1]['assigned_bay'], new_plan[i1]['tier_level']
    new_plan[i1]['assigned_block'] = new_plan[i2]['assigned_block']
    new_plan[i1]['assigned_row'] = new_plan[i2]['assigned_row']
    new_plan[i1]['assigned_bay'] = new_plan[i2]['assigned_bay']
    new_plan[i1]['tier_level'] = new_plan[i2]['tier_level']
    new_plan[i2]['assigned_block'] = b1
    new_plan[i2]['assigned_row'] = r1
    new_plan[i2]['assigned_bay'] = bay1
    new_plan[i2]['tier_level'] = t1
    if not check_weight_stability(new_plan, container_map, yard_layout):
        return deepcopy(plan)
    return new_plan


def sqa_relocate_move(plan, container_map, yard_layout):
    if len(plan) < 1:
        return deepcopy(plan)
    new_plan = deepcopy(plan)
    idx = random.randint(0, len(new_plan) - 1)
    blocks = yard_layout['blocks']
    block = random.choice(blocks)
    block_id = block['block_id']
    max_tier = block['max_tier_height']
    stack_usage = {}
    for a in new_plan:
        if a['id'] != new_plan[idx]['id']:
            key = (a['assigned_block'], a['assigned_row'], a['assigned_bay'])
            stack_usage[key] = max(stack_usage.get(key, 0), a['tier_level'] + 1)
    row = random.randint(0, block['rows'] - 1)
    bay = random.randint(0, block['bays_per_row'] - 1)
    key = (block_id, row, bay)
    tier = stack_usage.get(key, 0)
    if tier < max_tier:
        new_plan[idx]['assigned_block'] = block_id
        new_plan[idx]['assigned_row'] = row
        new_plan[idx]['assigned_bay'] = bay
        new_plan[idx]['tier_level'] = tier
        if check_weight_stability(new_plan, container_map, yard_layout):
            return new_plan
    return deepcopy(plan)


def sqa_vessel_aware_relocate(plan, container_map, yard_layout, vessel_block_map):
    """Relocate a container toward a stack where its vessel-mates are."""
    if len(plan) < 1:
        return deepcopy(plan)
    new_plan = deepcopy(plan)
    idx = random.randint(0, len(new_plan) - 1)
    cid = new_plan[idx]['id']
    vid = container_map[cid]['vessel_id']
    # Find stacks that have vessel-mates
    vessel_stacks = {}
    for a in new_plan:
        if a['id'] != cid and container_map[a['id']]['vessel_id'] == vid:
            key = (a['assigned_block'], a['assigned_row'], a['assigned_bay'])
            if key not in vessel_stacks:
                vessel_stacks[key] = 0
            vessel_stacks[key] = max(vessel_stacks.get(key, 0), a['tier_level'] + 1)
    if not vessel_stacks:
        return sqa_relocate_move(plan, container_map, yard_layout)
    # Pick a nearby stack (same block, adjacent row/bay)
    mate_locations = list(vessel_stacks.keys())
    target_loc = random.choice(mate_locations)
    target_block_id = target_loc[0]
    block = None
    for b in yard_layout['blocks']:
        if b['block_id'] == target_block_id:
            block = b
            break
    if block is None:
        return sqa_relocate_move(plan, container_map, yard_layout)
    max_tier = block['max_tier_height']
    # Try adjacent positions to the vessel-mate
    stack_usage = {}
    for a in new_plan:
        if a['id'] != cid:
            key = (a['assigned_block'], a['assigned_row'], a['assigned_bay'])
            stack_usage[key] = max(stack_usage.get(key, 0), a['tier_level'] + 1)
    # Try the same stack first, then neighbors
    candidates = []
    tr, tb = target_loc[1], target_loc[2]
    for dr in range(-1, 2):
        for db in range(-1, 2):
            nr, nb = tr + dr, tb + db
            if 0 <= nr < block['rows'] and 0 <= nb < block['bays_per_row']:
                key = (target_block_id, nr, nb)
                tier = stack_usage.get(key, 0)
                if tier < max_tier:
                    candidates.append((key, tier))
    if not candidates:
        return sqa_relocate_move(plan, container_map, yard_layout)
    chosen, tier = random.choice(candidates)
    new_plan[idx]['assigned_block'] = chosen[0]
    new_plan[idx]['assigned_row'] = chosen[1]
    new_plan[idx]['assigned_bay'] = chosen[2]
    new_plan[idx]['tier_level'] = tier
    if check_weight_stability(new_plan, container_map, yard_layout):
        return new_plan
    return deepcopy(plan)


def simulated_quantum_annealing(initial_plan, containers, yard_layout, params, logger):
    P = params.get('trotter_slices', 25)
    num_sweeps = params.get('num_sweeps', 400)
    gamma_0 = params.get('initial_transverse_field', 4.0)
    gamma_final = params.get('final_transverse_field', 0.005)
    T_init = params.get('temperature_init', 8.0)
    T_final = params.get('temperature_final', 0.05)
    grouping_weight = params.get('grouping_weight', 0.5)
    balance_weight = params.get('balance_weight', 0.3)
    seed = params.get('random_seed', None)
    if seed is not None:
        random.seed(seed)
    container_map = {c['id']: c for c in containers}
    # Build vessel-to-preferred-block mapping (most common block per vessel)
    vessel_block_counts = {}
    for a in initial_plan:
        vid = container_map[a['id']]['vessel_id']
        if vid not in vessel_block_counts:
            vessel_block_counts[vid] = {}
        bid = a['assigned_block']
        vessel_block_counts[vid][bid] = vessel_block_counts[vid].get(bid, 0) + 1
    vessel_block_map = {}
    for vid, counts in vessel_block_counts.items():
        vessel_block_map[vid] = max(counts, key=counts.get)
    logger.info("=== Simulated Quantum Annealing (SQA) v1.2 ===")
    logger.info("Trotter replicas (P): " + str(P))
    logger.info("Monte Carlo sweeps: " + str(num_sweeps))
    logger.info("Transverse field: " + str(gamma_0) + " -> " + str(gamma_final))
    logger.info("Temperature: " + str(T_init) + " -> " + str(T_final))
    logger.info("Vessel-aware moves enabled")
    replicas = [deepcopy(initial_plan) for _ in range(P)]
    replica_energies = [compute_objective(r, containers, grouping_weight, balance_weight, yard_layout) for r in replicas]
    best_obj = min(replica_energies)
    best_plan = deepcopy(replicas[replica_energies.index(best_obj)])
    convergence_history = []
    tunnel_events = 0
    total_accepted = 0
    total_proposed = 0
    logger.info("Initial best energy: " + str(round(best_obj, 2)))
    for sweep in range(num_sweeps):
        progress = sweep / max(num_sweeps - 1, 1)
        gamma_t = gamma_0 * math.exp(-progress * math.log(max(gamma_0 / gamma_final, 1e-6)))
        T_t = T_init * math.exp(-progress * math.log(max(T_init / T_final, 1e-6)))
        beta_t = 1.0 / max(T_t, 1e-10)
        J_perp = -0.5 * T_t * math.log(max(math.tanh(gamma_t * beta_t / P), 1e-10))
        sweep_accepted = 0
        for p in range(P):
            # Adaptive move selection: early = more exploration, late = more vessel-aware refinement
            r = random.random()
            if progress < 0.3:
                # Early phase: 40% random swap, 30% random relocate, 20% vessel swap, 10% vessel relocate
                if r < 0.4:
                    candidate = sqa_swap_move(replicas[p], container_map, yard_layout)
                elif r < 0.7:
                    candidate = sqa_relocate_move(replicas[p], container_map, yard_layout)
                elif r < 0.9:
                    candidate = sqa_vessel_aware_swap(replicas[p], container_map, yard_layout, vessel_block_map)
                else:
                    candidate = sqa_vessel_aware_relocate(replicas[p], container_map, yard_layout, vessel_block_map)
            else:
                # Late phase: 20% random swap, 15% random relocate, 35% vessel swap, 30% vessel relocate
                if r < 0.2:
                    candidate = sqa_swap_move(replicas[p], container_map, yard_layout)
                elif r < 0.35:
                    candidate = sqa_relocate_move(replicas[p], container_map, yard_layout)
                elif r < 0.7:
                    candidate = sqa_vessel_aware_swap(replicas[p], container_map, yard_layout, vessel_block_map)
                else:
                    candidate = sqa_vessel_aware_relocate(replicas[p], container_map, yard_layout, vessel_block_map)
            candidate_energy = compute_objective(candidate, containers, grouping_weight, balance_weight, yard_layout)
            delta_E_classical = candidate_energy - replica_energies[p]
            delta_E_quantum = 0.0
            if P > 1 and gamma_t > 0.01:
                p_prev = (p - 1) % P
                p_next = (p + 1) % P
                def replica_overlap(plan_a, plan_b):
                    set_a = set((a['id'], a['assigned_block'], a['assigned_row'], a['assigned_bay']) for a in plan_a)
                    return sum(1 for a in plan_b if (a['id'], a['assigned_block'], a['assigned_row'], a['assigned_bay']) in set_a)
                overlap_prev_old = replica_overlap(replicas[p], replicas[p_prev])
                overlap_next_old = replica_overlap(replicas[p], replicas[p_next])
                overlap_prev_new = replica_overlap(candidate, replicas[p_prev])
                overlap_next_new = replica_overlap(candidate, replicas[p_next])
                delta_E_quantum = -J_perp * ((overlap_prev_new + overlap_next_new) - (overlap_prev_old + overlap_next_old))
            delta_E = delta_E_classical + delta_E_quantum
            total_proposed += 1
            accept = False
            if delta_E < 0:
                accept = True
            else:
                try:
                    accept = random.random() < math.exp(-delta_E * beta_t / P)
                except (OverflowError, ValueError):
                    accept = False
            if accept:
                if delta_E_classical > 0 and delta_E < 0:
                    tunnel_events += 1
                replicas[p] = candidate
                replica_energies[p] = candidate_energy
                total_accepted += 1
                sweep_accepted += 1
                if candidate_energy < best_obj:
                    best_obj = candidate_energy
                    best_plan = deepcopy(candidate)
        avg_energy = sum(replica_energies) / P
        energy_spread = max(replica_energies) - min(replica_energies)
        convergence_history.append({'sweep': sweep, 'best_energy': round(best_obj, 2), 'avg_replica_energy': round(avg_energy, 2), 'energy_spread': round(energy_spread, 2), 'transverse_field': round(gamma_t, 4), 'temperature': round(T_t, 4), 'acceptance_rate': round(sweep_accepted / max(P, 1), 3), 'tunnel_events_cumulative': tunnel_events})
        if sweep % max(1, num_sweeps // 5) == 0:
            logger.info("Sweep " + str(sweep) + "/" + str(num_sweeps) + " | Best=" + str(round(best_obj, 2)) + " | Avg=" + str(round(avg_energy, 2)) + " | Spread=" + str(round(energy_spread, 2)) + " | Gamma=" + str(round(gamma_t, 3)) + " | Tunnels=" + str(tunnel_events))
    acceptance_rate = total_accepted / max(total_proposed, 1)
    logger.info("SQA complete. Best=" + str(round(best_obj, 2)) + " | Tunnels=" + str(tunnel_events) + " | Accept=" + str(round(acceptance_rate * 100, 1)) + "%")
    quantum_metrics = {'trotter_slices': P, 'total_sweeps': num_sweeps, 'tunnel_events': tunnel_events, 'tunnel_rate': round(tunnel_events / max(total_proposed, 1), 4), 'acceptance_rate': round(acceptance_rate, 4), 'final_transverse_field': round(gamma_t, 6), 'final_temperature': round(T_t, 6), 'final_energy_spread': round(energy_spread, 2), 'quantum_advantage_indicator': round(tunnel_events / max(num_sweeps, 1), 3)}
    return best_plan, best_obj, convergence_history, quantum_metrics


def local_search_refinement(plan, containers, yard_layout, params, logger):
    """Post-SQA local search: try all pairwise swaps, accept improvements."""
    container_map = {c['id']: c for c in containers}
    grouping_weight = params.get('grouping_weight', 0.5)
    balance_weight = params.get('balance_weight', 0.3)
    best_plan = deepcopy(plan)
    best_obj = compute_objective(best_plan, containers, grouping_weight, balance_weight, yard_layout)
    improvements = 0
    n = len(best_plan)
    for i in range(n):
        for j in range(i + 1, n):
            # Skip if same vessel (swap won't help grouping)
            vid_i = container_map[best_plan[i]['id']]['vessel_id']
            vid_j = container_map[best_plan[j]['id']]['vessel_id']
            if vid_i == vid_j:
                continue
            # Try swap
            trial = deepcopy(best_plan)
            bi, ri, bayi, ti = trial[i]['assigned_block'], trial[i]['assigned_row'], trial[i]['assigned_bay'], trial[i]['tier_level']
            trial[i]['assigned_block'] = trial[j]['assigned_block']
            trial[i]['assigned_row'] = trial[j]['assigned_row']
            trial[i]['assigned_bay'] = trial[j]['assigned_bay']
            trial[i]['tier_level'] = trial[j]['tier_level']
            trial[j]['assigned_block'] = bi
            trial[j]['assigned_row'] = ri
            trial[j]['assigned_bay'] = bayi
            trial[j]['tier_level'] = ti
            if not check_weight_stability(trial, container_map, yard_layout):
                continue
            trial_obj = compute_objective(trial, containers, grouping_weight, balance_weight, yard_layout)
            if trial_obj < best_obj - 0.001:
                best_plan = trial
                best_obj = trial_obj
                improvements += 1
    logger.info("Local search: " + str(improvements) + " improvements, objective " + str(round(best_obj, 2)))
    return best_plan, best_obj, improvements


def multi_restart_sqa(initial_plan, containers, yard_layout, params, logger):
    """Run SQA multiple times with different seeds, return the best result."""
    num_restarts = params.get('num_restarts', 3)
    sweeps_per_restart = params.get('num_sweeps', 150)
    # Override sweeps for each restart
    restart_params = dict(params)
    restart_params['num_sweeps'] = sweeps_per_restart
    best_plan = None
    best_obj = float('inf')
    best_convergence = []
    best_quantum_metrics = None
    total_tunnel_events = 0
    all_convergence = []
    for restart in range(num_restarts):
        restart_params['random_seed'] = (params.get('random_seed', 42) or 42) + restart * 1000
        logger.info("--- SQA Restart " + str(restart + 1) + "/" + str(num_restarts) + " (seed=" + str(restart_params['random_seed']) + ") ---")
        plan, obj, convergence, qmetrics = simulated_quantum_annealing(initial_plan, containers, yard_layout, restart_params, logger)
        total_tunnel_events += qmetrics['tunnel_events']
        all_convergence.extend(convergence)
        if obj < best_obj:
            best_obj = obj
            best_plan = plan
            best_convergence = convergence
            best_quantum_metrics = qmetrics
            logger.info("New best from restart " + str(restart + 1) + ": " + str(round(obj, 2)))
    # Merge tunnel events across restarts
    best_quantum_metrics['tunnel_events'] = total_tunnel_events
    best_quantum_metrics['total_sweeps'] = sweeps_per_restart * num_restarts
    best_quantum_metrics['num_restarts'] = num_restarts
    best_quantum_metrics['tunnel_rate'] = round(total_tunnel_events / max(sweeps_per_restart * num_restarts * best_quantum_metrics['trotter_slices'], 1), 4)
    best_quantum_metrics['quantum_advantage_indicator'] = round(total_tunnel_events / max(sweeps_per_restart * num_restarts, 1), 3)
    return best_plan, best_obj, best_convergence, best_quantum_metrics


def compute_output_metrics(stacking_plan, containers, yard_layout):
    container_map = {c['id']: c for c in containers}
    total_reshuffles, reshuffles_per_vessel = compute_reshuffles_for_stacking(stacking_plan, containers)
    block_util = compute_block_utilization(stacking_plan, yard_layout)
    grouping_score = compute_vessel_grouping_score(stacking_plan, containers)
    balance_score = compute_weight_balance_score(stacking_plan, container_map, yard_layout)
    return {'total_reshuffles': total_reshuffles, 'average_reshuffles_per_vessel': total_reshuffles / len(reshuffles_per_vessel) if reshuffles_per_vessel else 0.0, 'max_reshuffles_single_vessel': max(reshuffles_per_vessel.values()) if reshuffles_per_vessel else 0, 'vessel_grouping_score': grouping_score, 'weight_balance_score': balance_score, 'block_utilization': block_util, 'reshuffles_per_vessel': reshuffles_per_vessel}


def run(input_data, solver_params, extra_arguments):
    """
    Entry point for QCentroid solver.
    
    input_data: dict with 'containers' and 'yard_layout'
    solver_params: dict with algorithm parameters (trotter_slices, num_sweeps, etc.)
    extra_arguments: dict with optional overrides
    
    Returns: dict with 'stacking_plan', 'objective', and 'metrics'
    """
    start_time = time.time()
    logger = qcentroid_user_log
    
    containers = input_data.get('containers', [])
    yard_layout = input_data.get('yard_layout', {})
    
    # Merge solver_params with extra_arguments
    params = dict(solver_params)
    if extra_arguments:
        params.update(extra_arguments)
    
    logger.info("QCentroid v1.3 starting with " + str(len(containers)) + " containers")
    
    # Greedy init
    initial_plan = greedy_initial_stacking(containers, yard_layout, logger)
    initial_obj = compute_objective(initial_plan, containers, params.get('grouping_weight', 0.5), params.get('balance_weight', 0.3), yard_layout)
    logger.info("Initial greedy solution objective: " + str(round(initial_obj, 2)))
    
    # Multi-restart SQA
    num_restarts = params.get('num_restarts', 3)
    logger.info("Running multi-restart SQA (" + str(num_restarts) + " restarts)")
    sqa_plan, sqa_obj, convergence_history, quantum_metrics = multi_restart_sqa(initial_plan, containers, yard_layout, params, logger)
    
    # Post-SQA local search refinement
    logger.info("Running post-SQA local search refinement...")
    final_plan, final_obj, improvements = local_search_refinement(sqa_plan, containers, yard_layout, params, logger)
    logger.info("Final solution objective: " + str(round(final_obj, 2)))
    
    # Compute metrics
    metrics = compute_output_metrics(final_plan, containers, yard_layout)
    
    # Showcase best 3 improvements
    showcase_improvements = []
    if improvements > 0:
        sample_size = min(3, len(convergence_history))
        if sample_size > 0:
            for i in range(sample_size):
                idx = int(i * len(convergence_history) / sample_size)
                if idx < len(convergence_history):
                    showcase_improvements.append(convergence_history[idx])
    
    elapsed_s = time.time() - start_time
    
    return {
        'stacking_plan': final_plan,
        'objective': round(final_obj, 2),
        'metrics': metrics,
        'solver_metadata': {
            'version': 'v1.3',
            'algorithm': 'SQA_SuzukiTrotter_MultiRestart_LocalSearch',
            'initial_objective': round(initial_obj, 2),
            'sqa_best_objective': round(sqa_obj, 2),
            'local_search_improvements': improvements,
            'final_objective': round(final_obj, 2),
            'convergence_sample': showcase_improvements,
            'quantum_metrics': quantum_metrics,
            'computation_metrics': {'wall_time_s': round(elapsed_s, 3), 'algorithm': 'SQA_SuzukiTrotter_v1.3'}
        }
    }
