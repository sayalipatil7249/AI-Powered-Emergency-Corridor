"""Deterministic pre-trip coordination; the live referee remains authoritative.

Times are estimates, with a conservative 20 s junction occupancy window.
Only identical movements are assumed to share a green safely.
"""
from corridor import priority

CLEARANCE_SECONDS = 20.0


def schedule(routes):
    """Schedule signal visits, propagating each wait to downstream junctions."""
    delays = [0.0] * len(routes)
    indices = [0] * len(routes)
    reservations = {}
    visits = []
    while True:
        pending = [
            (route['visits'][indices[i]]['seconds'] + delays[i], i)
            for i, route in enumerate(routes) if indices[i] < len(route['visits'])
        ]
        if not pending:
            break
        arrival, i = min(pending)
        visit = routes[i]['visits'][indices[i]]
        # Near-simultaneous competing requests: minimize weighted waiting.
        contenders = [(arrival, i)]
        for other_arrival, j in pending:
            other = routes[j]['visits'][indices[j]]
            if j != i and other['signal_id'] == visit['signal_id'] and other_arrival < arrival + CLEARANCE_SECONDS:
                contenders.append((other_arrival, j))
        def cost(first):
            at, who = first
            movement = routes[who]['visits'][indices[who]]['movement']
            return (sum(
                max(0, at + CLEARANCE_SECONDS - t) * routes[j]['weight']
                for t, j in contenders
                if j != who and routes[j]['visits'][indices[j]]['movement'] != movement
            ), -routes[who]['weight'], at, who)
        arrival, i = min(contenders, key=cost)
        visit = routes[i]['visits'][indices[i]]
        previous = reservations.get(visit['signal_id'])
        wait = 0.0
        if previous and previous['movement'] != visit['movement']:
            wait = max(0, previous['until'] - arrival)
        delays[i] += wait
        passing = arrival + wait
        reservations[visit['signal_id']] = {
            'movement': visit['movement'],
            'until': max(passing + CLEARANCE_SECONDS,
                         previous['until'] if previous else 0),
        }
        visits.append({**visit, 'ambulance': routes[i]['label'],
                       'arrival_seconds': round(arrival, 1),
                       'passing_seconds': round(passing, 1),
                       'wait_seconds': round(wait, 1)})
        indices[i] += 1
    return {
        'weighted_seconds': sum((r['seconds'] + d) * r['weight'] for r, d in zip(routes, delays)),
        'delays': delays,
        'visits': visits,
    }


def select_route(candidates, existing, condition, label):
    """Compare bounded alternatives by total priority-weighted fleet time."""
    weight = priority.weight(condition)
    evaluated = []
    for candidate in candidates:
        route = {**candidate, 'weight': weight, 'label': label}
        result = schedule([*existing, route])
        evaluated.append((result['weighted_seconds'], route, result))
    best = min(evaluated, key=lambda item: item[0])
    baseline = evaluated[0]
    changed = best[1]['roads'] != baseline[1]['roads']
    return best[1], {
        'changed': changed,
        'estimated_wait_seconds': round(best[2]['delays'][-1], 1),
        'weighted_seconds_saved': round(max(0, baseline[0] - best[0]), 1),
        'estimated_seconds': round(best[1]['seconds'] + best[2]['delays'][-1], 1),
        'alternatives_checked': len(candidates),
        'estimate': True,
    }
