"""Integration checks against the actual Pune network; no SUMO process or DB writes."""
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from fastapi import HTTPException
from backend.api.routes.fleet import FleetStart, prepare_fleet, preview_fleet, start_fleet
from simulation.sumo import route_planner


class FleetPlanningTests(unittest.TestCase):
    def test_preview_matches_dispatch_and_every_movement_is_legal(self):
        request = FleetStart(ambulances=[
            {'kind': 'demo', 'condition': 'cardiac_arrest'},
            {'kind': 'crossing', 'condition': 'stable'},
            {'kind': 'crossing', 'condition': 'stroke'},
        ])
        preview = preview_fleet(request)
        with patch('backend.api.routes.fleet.simulation_service.start', return_value={'status':'starting'}) as dispatch:
            start_fleet(request)
        args, kwargs = dispatch.call_args
        trips = [args[0], *(trip for trip, _ in kwargs['extra'])]
        self.assertEqual(len(preview['ambulances']), 3)
        for shown, trip in zip(preview['ambulances'], trips):
            self.assertEqual(shown['geometry'], route_planner.roads_geometry(trip['roads']))
            self.assertEqual(shown['routing_decision'], trip['routing_decision'])
            net = route_planner._net()
            for road, following in zip(trip['roads'], trip['roads'][1:]):
                self.assertIn(net.getEdge(following), net.getEdge(road).getAllowedOutgoing('emergency'))

    def test_shared_markers_include_both_requests(self):
        preview = preview_fleet(FleetStart(ambulances=[{'kind':'demo'}, {'kind':'demo'}]))
        self.assertGreater(len(preview['shared_junctions']), 0)
        for junction in preview['shared_junctions']:
            self.assertEqual({v['ambulance'] for v in junction['visits']}, {'Ambulance 1', 'Ambulance 2'})
            self.assertTrue(all(v['wait_seconds'] == 0 for v in junction['visits']))

    def test_request_validation(self):
        for ambulances in ([], [{'kind':'crossing'}], [{'kind':'trip'}],
                           [{'kind':'demo', 'condition':'unknown'}], [{'kind':'demo'}] * 11):
            with self.subTest(ambulances=ambulances), self.assertRaises(HTTPException) as error:
                prepare_fleet(FleetStart(ambulances=ambulances))
            self.assertEqual(error.exception.status_code, 400)

    def test_live_request_uses_remaining_active_routes(self):
        from backend.services.simulation_service import simulation_service
        trips, _ = prepare_fleet(FleetStart(ambulances=[{'kind':'demo'}]))
        trip = trips[0]
        active = SimpleNamespace(
            phase='driving', trip=trip, dispatch_at=0, label='Ambulance 1',
            condition='stroke', _stood_still=lambda: 0,
            ambulance=SimpleNamespace(route=lambda: trip['roads']),
        )
        context = SimpleNamespace(runs=[active, SimpleNamespace(phase='arrived')],
                                  phase='driving', now=1000)
        summary = {'label':'Ambulance 3', 'condition_label':'Stable patient'}
        with patch.object(simulation_service, 'run_in_simulation', side_effect=lambda action: action(context)), \
             patch.object(simulation_service, '_new_run', return_value=SimpleNamespace(summary=lambda: summary)) as dispatch, \
             patch.object(simulation_service, 'add_agent_message'), \
             patch('backend.services.simulation_service.route_position', return_value=(10, 5)), \
             patch.object(route_planner, 'coordinate_trip', wraps=route_planner.coordinate_trip) as coordinate:
            simulation_service.add_ambulance(trip, 'stable')
        existing = coordinate.call_args.args[2]
        self.assertEqual(len(existing), 1)
        self.assertEqual(existing[0]['roads'], trip['roads'][10:])
        self.assertEqual(existing[0]['weight'], 3)
        self.assertIn('routing_decision', dispatch.call_args.args[0])
        self.assertEqual(dispatch.call_args.args[2], 1001)

    def test_partial_edge_timing(self):
        trip, _ = prepare_fleet(FleetStart(ambulances=[{'kind':'demo'}]))
        edge = route_planner._net().getEdge(trip[0]['roads'][0])
        profile = route_planner.corridor_profile({'roads':[edge.getID()], 'depart_position':10, 'arrival_position':20})
        self.assertAlmostEqual(profile['seconds'], 10 / edge.getSpeed() * 1.3)
        self.assertEqual(profile['visits'], [])

    def test_jam_detour_uses_legal_roads_and_skips_blocked_edge(self):
        trip, _ = prepare_fleet(FleetStart(ambulances=[{'kind': 'demo'}]))
        original = trip[0]['roads']
        blocked = {original[len(original) // 2]}
        detour = route_planner.avoiding_roads(original[0], original[-1], blocked)
        self.assertIsNotNone(detour)
        self.assertEqual((detour[0], detour[-1]), (original[0], original[-1]))
        self.assertFalse(blocked.intersection(detour))
        net = route_planner._net()
        for road, following in zip(detour, detour[1:]):
            self.assertIn(net.getEdge(following),
                          net.getEdge(road).getAllowedOutgoing('emergency'))

    def test_second_ambulance_exposes_its_own_live_view(self):
        from backend.services.simulation_service import simulation_service
        snapshot = {'vehicle_id': 'ambulance_02', 'latitude': 18.52,
                    'longitude': 73.85, 'route_index': 3, 'speed': 10}
        signals = [{'signal_id': 'second-route-junction'}]
        run = SimpleNamespace(
            snapshot=snapshot, vehicle_id='ambulance_02', phase='driving',
            engine=SimpleNamespace(route_signals=signals), upcoming=[],
            route_cache=object(), road_shapes={}, response=None, police_watch=None,
            trip={'police_along_route': [{'name': 'Station for Ambulance 2'}]},
        )
        with patch('backend.services.simulation_service.feed.signal_states', return_value=[]) as states, \
             patch('backend.services.simulation_service.feed.corridor_entries', return_value=[]), \
             patch('backend.services.simulation_service.feed.route_traffic_segments', return_value=[]):
            detail = simulation_service._ambulance_detail(run, object(), object())
        self.assertIs(detail['snapshot'], snapshot)
        self.assertIs(detail['route_signals'], signals)
        self.assertEqual(detail['police_along_route'][0]['name'], 'Station for Ambulance 2')
        states.assert_called_once()


if __name__ == '__main__':
    unittest.main()
