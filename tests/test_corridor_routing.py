import unittest
from corridor.routing import schedule, select_route


def route(roads=('main',), seconds=100, arrival=40, movement=('west', 'east'), weight=1, label='A'):
    return {'roads': list(roads), 'seconds': seconds, 'weight': weight, 'label': label,
            'visits': [] if arrival is None else [
                {'signal_id': 'J', 'seconds': arrival, 'movement': movement}]}


class RoutingTests(unittest.TestCase):
    def test_critical_gets_shared_junction_first(self):
        result = schedule([route(label='Stable'), route(movement=('north', 'south'), weight=3, label='Critical')])
        self.assertEqual(result['delays'], [20, 0])

    def test_same_movement_can_share_green(self):
        self.assertEqual(schedule([route(), route(label='B')])['delays'], [0, 0])

    def test_separated_arrivals_do_not_conflict(self):
        self.assertEqual(schedule([route(), route(arrival=100, movement=('n', 's'))])['delays'], [0, 0])

    def test_wait_is_better_than_long_detour(self):
        existing = [route(weight=3, label='Critical')]
        main = route(movement=('n', 's'))
        detour = route(('detour',), seconds=150, arrival=None)
        selected, decision = select_route([main, detour], existing, 'stable', 'New')
        self.assertEqual(selected['roads'], ['main'])
        self.assertEqual(decision['estimated_wait_seconds'], 20)
        self.assertFalse(decision['changed'])

    def test_short_detour_beats_wait(self):
        selected, decision = select_route(
            [route(movement=('n', 's')), route(('detour',), seconds=105, arrival=None)],
            [route(weight=3)], 'stable', 'New')
        self.assertEqual(selected['roads'], ['detour'])
        self.assertEqual(decision['weighted_seconds_saved'], 15)

    def test_delay_to_existing_ambulance_counts(self):
        # New critical crew can avoid delaying a stable crew for just 5 s
        # more driving: 5*3 < 20*1.
        selected, _ = select_route(
            [route(movement=('n', 's')), route(('detour',), seconds=105, arrival=None)],
            [route()], 'stroke', 'New')
        self.assertEqual(selected['roads'], ['detour'])

    def test_wait_propagates_downstream(self):
        low = route(label='Low')
        low['visits'].append({'signal_id':'K', 'seconds':60, 'movement':('w','e')})
        result = schedule([low, route(weight=3, movement=('n','s'))])
        downstream = next(v for v in result['visits'] if v['signal_id'] == 'K')
        self.assertEqual(downstream['arrival_seconds'], 80)

    def test_no_corridors_retains_route(self):
        selected, decision = select_route([route()], [], 'stable', 'A')
        self.assertFalse(decision['changed'])
        self.assertEqual(decision['estimated_wait_seconds'], 0)


if __name__ == '__main__':
    unittest.main()
