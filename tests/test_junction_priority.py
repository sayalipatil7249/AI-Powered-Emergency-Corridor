import unittest
from types import SimpleNamespace

from corridor import priority
from corridor.referee import JunctionReferee
from backend.services.ambulance_run import AmbulanceRun


class JunctionPriorityTests(unittest.TestCase):
    def setUp(self):
        self.referee = JunctionReferee()
        self.referee.register('critical', 'Ambulance 1', 'cardiac_arrest')
        self.referee.register('stable', 'Ambulance 2', 'stable', stood_still=lambda: 1200)

    def claim(self, who, arrival, lead):
        links, target = ({0}, 'Gr') if who == 'critical' else ({1}, 'rG')
        self.referee.claim(who, {
            'signal_id': 'J', 'name': 'Shared junction', 'links': links,
            'target': target, 'arrival_seconds': arrival,
            'lead_seconds': lead,
        }, 0)
        return links, target

    def test_waiting_cannot_promote_stable_above_critical(self):
        self.assertLess(priority.weight('stable', 1200), priority.weight('serious'))
        self.assertLess(priority.weight('serious', 1200), priority.weight('stroke'))

    def test_critical_wins_conflicting_green_even_if_stable_arrives_first(self):
        critical = self.claim('critical', 8, 5)
        stable = self.claim('stable', 0, 5)
        self.assertEqual(self.referee.acquire('stable', 'J', *stable, 0), 'wait')
        self.assertEqual(self.referee.acquire('critical', 'J', *critical, 0), 'go')
        self.assertEqual(self.referee.owner('J'), 'critical')

    def test_earlier_non_conflicting_passage_is_allowed(self):
        critical = self.claim('critical', 60, 5)
        stable = self.claim('stable', 0, 5)
        self.assertEqual(self.referee.acquire('stable', 'J', *stable, 0), 'go')
        self.assertEqual(self.referee.acquire('critical', 'J', *critical, 0), 'wait')

    def test_signal_already_switching_is_not_taken_unsafely(self):
        stable = self.claim('stable', 0, 5)
        self.assertEqual(self.referee.acquire('stable', 'J', *stable, 0), 'go')
        critical = self.claim('critical', 8, 5)
        self.assertEqual(self.referee.acquire('critical', 'J', *critical, 1), 'wait')

    def test_stopped_critical_shows_road_jam_instead_of_give_way(self):
        run = object.__new__(AmbulanceRun)
        run.vehicle_id, run.number, run.label = 'ambulance_01', 1, 'Ambulance 1'
        run.condition = 'cardiac_arrest'
        run.arrival_time = None
        run.route_cache = object()
        run.snapshot = {'speed': 0.0, 'latitude': 18.52, 'longitude': 73.85}
        run.trip = {'start_name': 'Start', 'hospital_name': 'Hospital'}
        run.engine = SimpleNamespace(
            next_timing={'stage': 'green', 'queued_ahead_per_lane': 2},
            give_way=None, advised_speed=None, route_signals=[],
        )
        run.police_watch = SimpleNamespace(alerts={1: {'status': 'ON_SCENE'}})
        run.route_geometry = []
        summary = run.summary()
        self.assertIn('police', summary['delay_reason'])
        self.assertIsNone(summary['give_way'])



if __name__ == "__main__":
    unittest.main()
