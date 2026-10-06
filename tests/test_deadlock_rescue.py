"""Observed critical stalls can trigger a safe detour despite active police."""
import unittest
from types import SimpleNamespace

from corridor.response import DeadlockResponse


class Ambulance:
    vehicle_id = 'ambulance_01'

    def is_on_road(self):
        return True

    def speed(self):
        return 0.0

    def at_stop(self):
        return False

    def road_id(self):
        return 'A'

    def route(self):
        return ['A', 'B', 'C']

    def route_index(self):
        return 0


class Responder:
    def __init__(self):
        self.diverted = False
        self.removed = False
        self.released = False

    def find_route_avoiding(self, start, end, blocked):
        return {'roads': ['A', 'D', 'C'], 'seconds': 160} if blocked == {'B'} else None

    def reroute_ambulance(self, vehicle_id, roads):
        self.diverted = (vehicle_id, roads) == ('ambulance_01', ['A', 'D', 'C'])
        return self.diverted

    def unit_state(self, unit_id, target_road):
        return {'pending': True, 'arrived': False}

    def remove_unit(self, unit_id):
        self.removed = True

    def release_junctions(self):
        self.released = True


class CriticalRescueTests(unittest.TestCase):
    def make_response(self, critical=True, jam_start=1, alerts=()):
        responder = Responder()
        traffic = SimpleNamespace(
            lane_shape=lambda _: [(0, 0)],
            to_latlon=lambda x, y: {'latitude': 18.5, 'longitude': 73.8},
        )
        response = DeadlockResponse(
            Ambulance(), traffic, responder, [],
            route_ahead=lambda _: ({}, {
                'start_index': jam_start, 'end_index': jam_start,
                'distance': 100, 'length': 50,
            }),
            predict=lambda _: (0.9, 120), traffic_level=0,
            route_seconds=lambda roads: {('A', 'B', 'C'): 300,
                                         ('A', 'D', 'C'): 160}.get(tuple(roads)),
            critical_can_reroute=lambda: critical,
            police_alerts=lambda: alerts,
        )
        response.set_route({'roads': ['A', 'B', 'C']})
        response.status = 'police_en_route'
        response.police = {
            'unit_id': 'police_1', 'target_road': 'B', 'stage': 'en_route',
            'eta_seconds': 100, 'initial_eta_seconds': 100,
            'sent_at': 0, 'jam_roads': ['B'],
        }
        return response, responder

    def test_critical_can_divert_while_police_are_en_route(self):
        response, responder = self.make_response()
        self.assertIsNone(response.step(0))
        self.assertIsNone(response.step(44))
        self.assertEqual(response.step(45), 'rerouted')
        self.assertTrue(responder.diverted)
        self.assertTrue(responder.removed)
        self.assertTrue(responder.released)
        self.assertEqual(response.decision['choice'], 'critical_rescue_reroute')

    def test_stable_ambulance_waits_for_police(self):
        response, responder = self.make_response(critical=False)
        response.step(0)
        self.assertIsNone(response.step(60))
        self.assertFalse(responder.diverted)

    def test_nearby_police_are_allowed_to_clear_first(self):
        response, responder = self.make_response()
        response.police['eta_seconds'] = 20
        response.step(0)
        self.assertIsNone(response.step(45))
        self.assertFalse(responder.diverted)

    def test_signal_less_watch_police_on_scene_get_a_chance(self):
        alerts = ({'road_id': 'B', 'status': 'ON_SCENE', 'on_scene_at': 20},)
        response, responder = self.make_response(alerts=alerts)
        response.police = None
        response.status = 'watching'
        response.step(0)
        self.assertIsNone(response.step(45))
        self.assertFalse(responder.diverted)

    def test_jam_on_current_road_cannot_be_avoided(self):
        response, responder = self.make_response(jam_start=0)
        response.step(0)
        self.assertIsNone(response.step(60))
        self.assertFalse(responder.diverted)


if __name__ == '__main__':
    unittest.main()
