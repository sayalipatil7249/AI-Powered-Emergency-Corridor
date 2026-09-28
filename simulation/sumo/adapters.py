"""
SUMO implementations of the corridor connectors (corridor/interfaces.py),
plus SumoSimulation, which starts SUMO and advances time.

This is the only module (with sumo_bridge.py) that talks to SUMO through
traci. To run the corridor on real signals, write new classes with the
same methods instead of changing the corridor code.
"""

import traci
from traci import constants as tc

from simulation.sumo.sumo_bridge import sumo_to_latlon, sumo_to_latlon_many

# What SumoTrafficSource reads for every road it is asked about.
_ROAD_VARIABLES = (
    tc.LAST_STEP_VEHICLE_HALTING_NUMBER,
    tc.LAST_STEP_VEHICLE_NUMBER,
    tc.LAST_STEP_MEAN_SPEED,
    tc.VAR_CURRENT_TRAVELTIME,
)

# What SumoTrafficSource.vehicles() reads for every vehicle.
_VEHICLE_VARIABLES = (
    tc.VAR_POSITION, tc.VAR_SPEED, tc.VAR_ANGLE, tc.VAR_ROAD_ID,
)


class SumoSimulation:
    """Starts, advances and stops a SUMO simulation."""

    _runs = 0

    def start(self, command):
        # A fresh connection label per run: after a SUMO crash the old
        # "default" connection can stay registered and block new starts.
        SumoSimulation._runs += 1
        SumoResponder.reset_held_lanes()
        _INCIDENTS.clear()
        traci.start(command, label=f"sim{SumoSimulation._runs}")

    def step(self):
        traci.simulationStep()

    def time(self):
        return traci.simulation.getTime()

    def expected_vehicles(self):
        """Vehicles still driving or waiting to depart."""
        return traci.simulation.getMinExpectedNumber()

    def arrived_ids(self):
        return traci.simulation.getArrivedIDList()

    def teleports_started(self):
        """Vehicles removed this step because they were stuck."""
        return traci.simulation.getStartingTeleportNumber()

    def close(self):
        # Also after a SUMO crash, so the next simulation can start.
        try:
            traci.close()
        except Exception:
            pass


class SumoAmbulanceTracker:
    def __init__(self, vehicle_id="ambulance_01"):
        self.vehicle_id = vehicle_id

    def dispatch(self, route, depart_position=None, arrival_position=None):
        """Add the ambulance to the simulation on this route, now."""

        route_id = f"{self.vehicle_id}_route"
        traci.route.add(route_id, list(route))
        traci.vehicle.add(
            self.vehicle_id,
            route_id,
            typeID="ambulance",
            depart="now",
            departPos=(
                str(depart_position) if depart_position is not None
                else "base"
            ),
            arrivalPos=(
                str(arrival_position) if arrival_position is not None
                else "max"
            ),
        )

    def is_on_road(self):
        return self.vehicle_id in traci.vehicle.getIDList()

    def position(self):
        x, y = traci.vehicle.getPosition(self.vehicle_id)
        return {"x": x, "y": y, **sumo_to_latlon(x, y)}

    def speed(self):
        return traci.vehicle.getSpeed(self.vehicle_id)

    def heading(self):
        return traci.vehicle.getAngle(self.vehicle_id)

    def acceleration(self):
        return traci.vehicle.getAcceleration(self.vehicle_id)

    def max_speed(self):
        return traci.vehicle.getMaxSpeed(self.vehicle_id)

    def route(self):
        return list(traci.vehicle.getRoute(self.vehicle_id))

    def route_index(self):
        return traci.vehicle.getRouteIndex(self.vehicle_id)

    def road_id(self):
        return traci.vehicle.getRoadID(self.vehicle_id)

    def lane_id(self):
        return traci.vehicle.getLaneID(self.vehicle_id)

    def lane_position(self):
        return traci.vehicle.getLanePosition(self.vehicle_id)

    def driving_distance_to(self, road_id, position):
        return traci.vehicle.getDrivingDistance(
            self.vehicle_id, road_id, position
        )

    def upcoming_signals(self):
        return traci.vehicle.getNextTLS(self.vehicle_id)

    def departure_time(self):
        return traci.vehicle.getDeparture(self.vehicle_id)

    def waiting_time(self):
        return traci.vehicle.getAccumulatedWaitingTime(self.vehicle_id)


class SumoTrafficSource:
    def __init__(self):
        # Vehicles subscribed to: SUMO then sends all their positions and
        # speeds in one message per step instead of one request per
        # vehicle and value (over 1,000 vehicles are on the roads).
        self._subscribed = set()

        # Roads subscribed to the same way (the ambulance route and
        # junctions are read every step).
        self._roads = set()

    def _road(self, road_id, variable, read):
        """A live road value: from the subscription (no request to SUMO)
        once available, otherwise asked directly with read(road_id)."""

        if road_id not in self._roads:
            traci.edge.subscribe(road_id, _ROAD_VARIABLES)
            self._roads.add(road_id)

        results = traci.edge.getSubscriptionResults(road_id)
        if variable in results:
            return results[variable]
        return read(road_id)

    def lane_length(self, lane_id):
        return traci.lane.getLength(lane_id)

    def lane_speed_limit(self, lane_id):
        return traci.lane.getMaxSpeed(lane_id)

    def lane_shape(self, lane_id):
        return traci.lane.getShape(lane_id)

    def road_travel_time(self, road_id):
        return self._road(
            road_id, tc.VAR_CURRENT_TRAVELTIME, traci.edge.getTraveltime
        )

    def road_halting_count(self, road_id):
        return self._road(
            road_id, tc.LAST_STEP_VEHICLE_HALTING_NUMBER,
            traci.edge.getLastStepHaltingNumber,
        )

    def road_vehicle_count(self, road_id):
        return self._road(
            road_id, tc.LAST_STEP_VEHICLE_NUMBER,
            traci.edge.getLastStepVehicleNumber,
        )

    def road_mean_speed(self, road_id):
        return self._road(
            road_id, tc.LAST_STEP_MEAN_SPEED, traci.edge.getLastStepMeanSpeed
        )

    def road_lane_count(self, road_id):
        return traci.edge.getLaneNumber(road_id)

    def lane_halting_count(self, lane_id):
        return traci.lane.getLastStepHaltingNumber(lane_id)

    def lane_vehicle_ids(self, lane_id):
        return list(traci.lane.getLastStepVehicleIDs(lane_id))

    def vehicle_count(self):
        return traci.vehicle.getIDCount()

    def vehicles(self, exclude=""):
        ids = set(traci.vehicle.getIDList())

        for vehicle_id in ids - self._subscribed:
            traci.vehicle.subscribe(vehicle_id, _VEHICLE_VARIABLES)
        self._subscribed = ids

        results = traci.vehicle.getAllSubscriptionResults()
        vehicle_ids = [
            vehicle_id for vehicle_id in results
            if vehicle_id != exclude and vehicle_id in ids
        ]
        positions = [results[vehicle_id][tc.VAR_POSITION] for vehicle_id in vehicle_ids]

        return [
            {
                "vehicle_id": vehicle_id,
                "x": x,
                "y": y,
                **coordinates,
                "speed": results[vehicle_id][tc.VAR_SPEED],
                "heading": round(results[vehicle_id][tc.VAR_ANGLE]),
                "road_id": results[vehicle_id][tc.VAR_ROAD_ID],
            }
            for vehicle_id, (x, y), coordinates in zip(
                vehicle_ids, positions, sumo_to_latlon_many(positions)
            )
        ]

    def to_latlon(self, x, y):
        return sumo_to_latlon(x, y)

    def snap_to_road(self, latitude, longitude):
        try:
            road_id, _, _ = traci.simulation.convertRoad(
                longitude, latitude, isGeo=True, vClass="passenger"
            )
        except traci.TraCIException:
            return None
        return None if road_id.startswith(":") else road_id

    def set_road_speed_limit(self, road_id, speed):
        traci.edge.setMaxSpeed(road_id, speed)


# Id of every signal's normal (fixed-time) program in the network.
NORMAL_PROGRAM = "0"


class SumoSignalController:
    def __init__(self):
        # Junction positions and wiring never change: read them once.
        self._positions = {}
        self._signal_ids = None
        self._links = {}

    def signal_ids(self):
        if self._signal_ids is None:
            self._signal_ids = traci.trafficlight.getIDList()
        return self._signal_ids

    def controlled_links(self, signal_id):
        # Which lanes a signal controls never changes during a run.
        if signal_id not in self._links:
            self._links[signal_id] = traci.trafficlight.getControlledLinks(
                signal_id
            )
        return self._links[signal_id]

    def light_states(self, signal_id):
        return traci.trafficlight.getRedYellowGreenState(signal_id)

    def phase(self, signal_id):
        return traci.trafficlight.getPhase(signal_id)

    def program(self, signal_id):
        return traci.trafficlight.getProgram(signal_id)

    def find_green_phase(self, signal_id, link_index):
        """
        First phase of the current program with full green ("G") for
        link_index, otherwise the first with permissive green ("g").
        """

        logic = self._normal_logic(signal_id)
        if logic is None:
            return None

        for light in ("G", "g"):
            for phase_index, phase in enumerate(logic.phases):
                if (
                    link_index < len(phase.state)
                    and phase.state[link_index] == light
                ):
                    return phase_index

        return None

    def _normal_logic(self, signal_id):
        return next(
            (
                item
                for item in traci.trafficlight.getAllProgramLogics(signal_id)
                if item.programID == NORMAL_PROGRAM
            ),
            None,
        )

    def phase_states(self, signal_id):
        logic = self._normal_logic(signal_id)
        return [phase.state for phase in logic.phases] if logic else []

    def phase_state(self, signal_id, phase):
        logic = self._normal_logic(signal_id)
        return logic.phases[phase].state if logic else None

    def set_light_states(self, signal_id, states):
        # Runs a temporary "online" program until hold_phase or
        # restore_normal switches back to the normal one.
        traci.trafficlight.setRedYellowGreenState(signal_id, states)

    def hold_phase(self, signal_id, phase):
        # Setting the phase every step restarts its timer, so it holds.
        if traci.trafficlight.getProgram(signal_id) != NORMAL_PROGRAM:
            traci.trafficlight.setProgram(signal_id, NORMAL_PROGRAM)
        traci.trafficlight.setPhase(signal_id, phase)

    def restore_normal(self, signal_id, held_phase=None):
        traci.trafficlight.setProgram(signal_id, NORMAL_PROGRAM)

        # Leave a held green through its own yellow phase, not by
        # jumping straight to red.
        logic = self._normal_logic(signal_id)
        if held_phase is not None and logic:
            following = (held_phase + 1) % len(logic.phases)
            if "y" in logic.phases[following].state.lower():
                traci.trafficlight.setPhase(signal_id, following)

    def name(self, signal_id, road_id=None):
        """Street names at the junction, seen from road_id, or None."""
        from simulation.sumo.route_planner import signal_name
        return signal_name(signal_id, road_id)

    def position(self, signal_id):
        """Average end point of the lanes entering the junction."""

        if signal_id not in self._positions:
            self._positions[signal_id] = self._compute_position(signal_id)

        return self._positions[signal_id]

    def _compute_position(self, signal_id):
        points = []

        for link_group in traci.trafficlight.getControlledLinks(signal_id):
            for link in link_group:
                if not link:
                    continue

                shape = traci.lane.getShape(link[0])
                if shape:
                    points.append(shape[-1])

        if not points:
            return None

        average_x = sum(point[0] for point in points) / len(points)
        average_y = sum(point[1] for point in points) / len(points)

        return sumo_to_latlon(average_x, average_y)


# Speed modes (traci.vehicle.setSpeedMode): normal driving, and "waved
# through by police": keep a safe distance but ignore right of way and
# red lights at junctions.
_NORMAL_SPEED_MODE = 31
_WAVED_THROUGH_SPEED_MODE = 7

# Police holding traffic back: incoming lanes limited to this (m/s).
_HELD_LANE_SPEED = 0.1


class SumoResponder:
    """Re-routing and traffic police in SUMO (corridor/interfaces.py
    Responder)."""

    # Lanes held back by any responder (the deadlock response and the
    # signal-less stretch watch each have one): lane id -> original
    # speed limit, and lane id -> responders holding it. A lane gets its
    # limit back only when the last one lets go.
    _original_limits = {}
    _holders = {}

    def __init__(self):
        self._held_lanes = set()   # lanes this responder holds back
        self._waved = set()        # vehicles whose speed mode was changed

    @classmethod
    def reset_held_lanes(cls):
        """New simulation: forget lanes held in the previous one."""
        cls._original_limits.clear()
        cls._holders.clear()

    def find_route(self, from_road, to_road, vehicle_type="ambulance"):
        try:
            stage = traci.simulation.findRoute(
                from_road, to_road, vType=vehicle_type,
                routingMode=tc.ROUTING_MODE_AGGREGATED,
            )
        except traci.TraCIException:
            return None
        if not stage.edges:
            return None
        return {"roads": list(stage.edges), "seconds": stage.travelTime}

    def route_seconds(self, roads):
        return sum(traci.edge.getTraveltime(road) for road in roads)

    def reroute_ambulance(self, vehicle_id, roads):
        try:
            traci.vehicle.setRoute(vehicle_id, roads)
            return True
        except traci.TraCIException:
            return False

    def send_unit(self, unit_id, roads):
        try:
            traci.route.add(f"{unit_id}_route", roads)
            traci.vehicle.add(
                unit_id, f"{unit_id}_route", typeID="police", depart="now",
                departLane="best", departSpeed="max",
            )
            return True
        except traci.TraCIException:
            return False

    def unit_state(self, unit_id, target_road):
        try:
            return self._unit_state(unit_id, target_road)
        except traci.TraCIException:
            return None

    def _unit_state(self, unit_id, target_road):
        if unit_id not in traci.vehicle.getIDList():
            # Still waiting for a gap to enter the road at the station.
            if unit_id in traci.simulation.getPendingVehicles():
                return {"pending": True, "arrived": False}
            return None
        x, y = traci.vehicle.getPosition(unit_id)
        road = traci.vehicle.getRoadID(unit_id)
        return {
            **sumo_to_latlon(x, y),
            "road_id": road,
            "arrived": road == target_road,
        }

    def remove_unit(self, unit_id):
        # Only if still there: it may already have reached the end of its
        # route and left the simulation.
        if (
            unit_id not in traci.vehicle.getIDList()
            and unit_id not in traci.simulation.getPendingVehicles()
        ):
            return
        try:
            # Stop its map updates first (SumoTrafficSource subscribes to
            # every vehicle); otherwise SUMO reports errors for it.
            traci.vehicle.unsubscribe(unit_id)
            traci.vehicle.remove(unit_id)
        except traci.TraCIException:
            pass

    def control_junctions(self, roads, keep_roads):
        from simulation.sumo.route_planner import _net

        net = _net()
        keep = set(keep_roads)
        roads_here = set(roads)
        waved = 0

        _police_clear_incidents(roads)

        for road in roads:
            try:
                node = net.getEdge(road).getToNode()
            except KeyError:
                continue
            # Stop traffic coming into the junction from other roads.
            for edge in node.getIncoming():
                if edge.getID() in keep:
                    continue
                for lane in edge.getLanes():
                    lane_id = lane.getID()
                    if lane_id not in self._held_lanes:
                        self._hold_lane(lane_id)
            # Wave through vehicles stuck inside the junction box (the
            # ones blocking everyone); the queues themselves then drain.
            for edge in node.getIncoming():
                for connections in edge.getOutgoing().values():
                    for connection in connections:
                        via = connection.getViaLaneID()
                        if via:
                            roads_here.add(via.rsplit("_", 1)[0])

        for road in roads_here:
            if not road.startswith(":"):
                continue
            try:
                vehicles = traci.edge.getLastStepVehicleIDs(road)
            except traci.TraCIException:
                continue
            for vehicle_id in vehicles:
                if vehicle_id in self._waved or vehicle_id.startswith("police"):
                    continue
                try:
                    traci.vehicle.setSpeedMode(vehicle_id, _WAVED_THROUGH_SPEED_MODE)
                except traci.TraCIException:
                    continue
                self._waved.add(vehicle_id)
                waved += 1
        return waved

    def _hold_lane(self, lane_id):
        if lane_id not in SumoResponder._original_limits:
            SumoResponder._original_limits[lane_id] = traci.lane.getMaxSpeed(lane_id)
            traci.lane.setMaxSpeed(lane_id, _HELD_LANE_SPEED)
        SumoResponder._holders.setdefault(lane_id, set()).add(id(self))
        self._held_lanes.add(lane_id)

    def release_junctions(self):
        for lane_id in self._held_lanes:
            holders = SumoResponder._holders.get(lane_id, set())
            holders.discard(id(self))
            if holders:
                continue  # another responder still holds it
            SumoResponder._holders.pop(lane_id, None)
            speed = SumoResponder._original_limits.pop(lane_id, None)
            if speed is None:
                continue
            try:
                traci.lane.setMaxSpeed(lane_id, speed)
            except traci.TraCIException:
                pass
        present = set(traci.vehicle.getIDList())
        for vehicle_id in self._waved & present:
            try:
                traci.vehicle.setSpeedMode(vehicle_id, _NORMAL_SPEED_MODE)
            except traci.TraCIException:
                pass
        self._held_lanes.clear()
        self._waved.clear()


# ---------------------------------------------------------
# Simulated accidents (the dashboard's "Simulate accident" button)
# ---------------------------------------------------------

# incident id -> {"road", "vehicles", "latitude", "longitude",
# "created_at", "police_since", "cleared_at", "cleared_by"}
_INCIDENTS = {}

# Crashed vehicles stay this long unless police clear them first (s).
INCIDENT_BLOCK_SECONDS = 900

# Officers on the road need this long to push the crashed vehicles
# aside (s).
POLICE_CLEAR_SECONDS = 45


class SumoIncidents:
    """A crash that blocks every lane of a road: broken-down vehicles
    standing still until traffic police clear them."""

    def block_road(self, road_id):
        now = traci.simulation.getTime()
        incident_id = f"incident_{len(_INCIDENTS) + 1}"
        length = traci.lane.getLength(f"{road_id}_0")
        depart_position = length * 0.5
        stop_position = length * 0.6

        vehicles = []
        for lane in range(traci.edge.getLaneNumber(road_id)):
            allowed = traci.lane.getAllowed(f"{road_id}_{lane}")
            if allowed and "passenger" not in allowed:
                continue  # footpath, bus lane...
            vehicle_id = f"{incident_id}_{lane}"
            try:
                traci.route.add(f"{vehicle_id}_route", [road_id])
                traci.vehicle.add(
                    vehicle_id, f"{vehicle_id}_route", depart="now",
                    departLane=str(lane), departPos=str(depart_position),
                    departSpeed="0",
                )
                traci.vehicle.setStop(
                    vehicle_id, road_id, pos=stop_position, laneIndex=lane,
                    duration=INCIDENT_BLOCK_SECONDS,
                )
                vehicles.append(vehicle_id)
            except traci.TraCIException:
                continue

        if not vehicles:
            return None

        x, y = traci.simulation.convert2D(road_id, stop_position)
        _INCIDENTS[incident_id] = {
            "incident_id": incident_id,
            "road_id": road_id,
            "vehicles": vehicles,
            **sumo_to_latlon(x, y),
            "created_at": now,
            "seen": False,         # vehicles enter SUMO at the next step
            "police_since": None,
            "cleared_at": None,
            "cleared_by": None,
        }
        return self._public(_INCIDENTS[incident_id])

    def summary(self):
        """Every incident of this run; notices crashes that ended by
        themselves (INCIDENT_BLOCK_SECONDS)."""

        if not _INCIDENTS:
            return []
        present = set(traci.vehicle.getIDList()) | set(
            traci.simulation.getPendingVehicles()
        )
        now = traci.simulation.getTime()
        for incident in _INCIDENTS.values():
            if incident["cleared_at"] is not None:
                continue
            here = any(vehicle in present for vehicle in incident["vehicles"])
            incident["seen"] = incident["seen"] or here
            # Gone after having been there: the crash ended by itself.
            if incident["seen"] and not here:
                incident["cleared_at"] = now
                incident["cleared_by"] = "time"
        return [self._public(incident) for incident in _INCIDENTS.values()]

    @staticmethod
    def _public(incident):
        return {
            k: v for k, v in incident.items() if k not in ("vehicles", "seen")
        }


def _police_clear_incidents(roads):
    """Officers at work on these roads push crashed vehicles aside after
    POLICE_CLEAR_SECONDS."""

    now = traci.simulation.getTime()
    for incident in _INCIDENTS.values():
        if incident["cleared_at"] is not None or incident["road_id"] not in roads:
            continue
        incident["police_since"] = incident["police_since"] or now
        if now - incident["police_since"] < POLICE_CLEAR_SECONDS:
            continue
        for vehicle_id in incident["vehicles"]:
            try:
                traci.vehicle.unsubscribe(vehicle_id)
                traci.vehicle.remove(vehicle_id)
            except traci.TraCIException:
                pass
        incident["cleared_at"] = now
        incident["cleared_by"] = "police"
