"""
SUMO implementations of the corridor connectors (corridor/interfaces.py),
plus SumoSimulation, which starts SUMO and advances time.

This is the only module (with sumo_bridge.py) that talks to SUMO through
traci. To run the corridor on real signals, write new classes with the
same methods instead of changing the corridor code.
"""

import zlib

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
        # Top speed before advise_speed() slowed it down, or None.
        self._normal_max_speed = None

    def dispatch(self, route, depart_position=None, arrival_position=None, stop=None):
        """Add the ambulance to the simulation on this route, now (with a
        planned stop, e.g. at the patient)."""

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
        # Like a real ambulance with its siren on: it does not wait for
        # cars stuck inside a junction box (gridlock), but it still stops
        # at red lights (so it can give way to another ambulance) and
        # keeps a safe distance to the car in front.
        traci.vehicle.setSpeedMode(self.vehicle_id, AMBULANCE_SPEED_MODE)
        if stop:
            self._plan_stop(stop)

    def _plan_stop(self, stop):
        road = stop["road"]
        length = traci.lane.getLength(f"{road}_0")
        position = min(max(stop["position"], 1.0), length - 1.0)
        # The first lane an ambulance may stop on (some are footpaths).
        for lane in range(traci.edge.getLaneNumber(road)):
            try:
                traci.vehicle.setStop(
                    self.vehicle_id, road, pos=position, laneIndex=lane,
                    duration=stop["seconds"],
                )
                return True
            except traci.TraCIException:
                continue
        return False

    def at_stop(self):
        try:
            return bool(traci.vehicle.isStopped(self.vehicle_id))
        except traci.TraCIException:
            return False

    def is_on_road(self):
        return self.vehicle_id in traci.vehicle.getIDList()

    def has_arrived(self):
        """True in the step it reached the end of its route. (Off the
        road but not arrived: SUMO is teleporting it past a jam.)"""
        return self.vehicle_id in traci.simulation.getArrivedIDList()

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
        if self._normal_max_speed is not None:
            return self._normal_max_speed
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

    def advise_speed(self, speed):
        # The simulated crew follows the advice exactly: a lower top speed.
        try:
            if speed is None:
                if self._normal_max_speed is not None:
                    traci.vehicle.setMaxSpeed(self.vehicle_id, self._normal_max_speed)
                self._normal_max_speed = None
                return
            if self._normal_max_speed is None:
                self._normal_max_speed = traci.vehicle.getMaxSpeed(self.vehicle_id)
            traci.vehicle.setMaxSpeed(self.vehicle_id, speed)
        except traci.TraCIException:
            self._normal_max_speed = None  # already left the simulation


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
        excluded = {exclude} if isinstance(exclude, str) else set(exclude)

        for vehicle_id in ids - self._subscribed:
            traci.vehicle.subscribe(vehicle_id, _VEHICLE_VARIABLES)
        self._subscribed = ids

        results = traci.vehicle.getAllSubscriptionResults()
        vehicle_ids = [
            vehicle_id for vehicle_id in results
            if vehicle_id not in excluded and vehicle_id in ids
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

    def queue_front(self, vehicle_id):
        try:
            chain = [vehicle_id]
            for _ in range(QUEUE_MAX_VEHICLES):
                leader = traci.vehicle.getLeader(chain[-1], 60)
                if (
                    not leader or not leader[0] or leader[1] > QUEUE_GAP_METERS
                    or traci.vehicle.getSpeed(leader[0]) > QUEUE_STOPPED_SPEED
                ):
                    break
                chain.append(leader[0])
            front = chain[-1]
            route = traci.vehicle.getRoute(front)
            index = traci.vehicle.getRouteIndex(front)
            road = traci.vehicle.getRoadID(front)
            if front == vehicle_id:
                kind = "ambulance"
            elif road.startswith(":"):
                kind = "box"
            else:
                upcoming = traci.vehicle.getNextTLS(front)
                kind = "signal" if upcoming and upcoming[0][2] < 20 else "junction"
        except traci.TraCIException:
            return None
        if not 0 <= index < len(route):
            return None
        return {
            "vehicle_id": front,
            "road_id": route[index],
            "next_road_id": route[index + 1] if index + 1 < len(route) else None,
            "kind": kind,
            "queue": len(chain) - 1,
        }

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


# Following a queue to its front: cars at most this far apart (m) and
# slower than this (m/s) are one queue; at most this many cars.
QUEUE_GAP_METERS = 15
QUEUE_STOPPED_SPEED = 0.5
QUEUE_MAX_VEHICLES = 80

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


# Ambulances: normal driving (31) plus bit 5 (32), "disregard right of
# way within intersections": vehicles already inside a junction no longer
# hold the ambulance up. Red lights and safe distances still apply.
AMBULANCE_SPEED_MODE = 31 | 32

# Speed modes (traci.vehicle.setSpeedMode): normal driving, and "waved
# through by police": keep a safe distance but ignore right of way and
# red lights at junctions.
_NORMAL_SPEED_MODE = 31
_WAVED_THROUGH_SPEED_MODE = 7

# Travel time given to roads a re-route must avoid (s): effectively
# closed for that one vehicle.
_AVOID_SECONDS = 100000

# Police holding traffic back: incoming lanes limited to this (m/s).
_HELD_LANE_SPEED = 0.1

# Officers wave on queued vehicles slower than this (m/s) on the roads
# they control.
_WAVE_QUEUE_SPEED = 0.5


class SumoResponder:
    """Re-routing and traffic police in SUMO (corridor/interfaces.py
    Responder)."""

    # Lanes held back by any responder (the deadlock response and the
    # signal-less stretch watch each have one): lane id -> original
    # speed limit, and lane id -> responders holding it. A lane gets its
    # limit back only when the last one lets go.
    _original_limits = {}
    _holders = {}

    # Roads ambulances are on or still have to drive: police never hold
    # these back, whichever ambulance they are working for (holding a side
    # road for one ambulance must not block another one on it).
    _ambulance_roads = set()

    def __init__(self):
        self._held_lanes = set()   # lanes this responder holds back
        self._waved = set()        # vehicles whose speed mode was changed

    @classmethod
    def reset_held_lanes(cls):
        """New simulation: forget lanes held in the previous one."""
        cls._original_limits.clear()
        cls._holders.clear()
        cls._ambulance_roads = set()

    @classmethod
    def protect_roads(cls, roads):
        """The roads every ambulance is on or still has to drive."""
        cls._ambulance_roads = set(roads)

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

    def find_route_avoiding(self, from_road, to_road, blocked):
        from simulation.sumo.route_planner import avoiding_roads

        roads = avoiding_roads(from_road, to_road, blocked)
        if not roads:
            return None
        return {"roads": roads, "seconds": self.route_seconds(roads)}

    def route_seconds(self, roads):
        return sum(traci.edge.getTraveltime(road) for road in roads)

    def reroute_ambulance(self, vehicle_id, roads):
        try:
            traci.vehicle.setRoute(vehicle_id, roads)
            return True
        except traci.TraCIException:
            return False

    def route_around(self, vehicle_id, avoid_roads):
        """Fastest route for the vehicle from the road it is on to its
        destination with current travel times, staying off avoid_roads:
        its road ids, or None. The vehicle keeps its route until
        reroute_ambulance() is called."""
        try:
            route = list(traci.vehicle.getRoute(vehicle_id))
            index = traci.vehicle.getRouteIndex(vehicle_id)
            if traci.vehicle.getRoadID(vehicle_id) != route[index]:
                return None  # inside a junction: ask again on the next road
        except traci.TraCIException:
            return None

        remaining = route[index:]
        # Only this vehicle sees the avoided roads as (nearly) closed.
        for road in avoid_roads:
            traci.vehicle.setAdaptedTraveltime(vehicle_id, road, _AVOID_SECONDS)
        try:
            traci.vehicle.rerouteTraveltime(vehicle_id, True)
            new_route = list(traci.vehicle.getRoute(vehicle_id))
            new_route = new_route[traci.vehicle.getRouteIndex(vehicle_id):]
        except traci.TraCIException:
            new_route = None
        finally:
            try:
                traci.vehicle.setRoute(vehicle_id, remaining)
            except traci.TraCIException:
                pass
            for road in avoid_roads:
                traci.vehicle.setAdaptedTraveltime(vehicle_id, road)

        if not new_route or new_route == remaining or set(new_route) & set(avoid_roads):
            return None
        return new_route

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
        keep = set(keep_roads) | SumoResponder._ambulance_roads
        roads_here = set(roads)
        waved = 0

        # A road held back earlier that an ambulance now needs: let it go.
        for lane_id in [
            lane for lane in self._held_lanes if lane.rsplit("_", 1)[0] in keep
        ]:
            self._release_lane(lane_id)

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
            try:
                vehicles = traci.edge.getLastStepVehicleIDs(road)
            except traci.TraCIException:
                continue
            if not road.startswith(":"):
                # The queue on the roads the officers control: stopped
                # vehicles are waved on through the junction (the other
                # directions are held back above).
                vehicles = [
                    vehicle_id for vehicle_id in vehicles
                    if traci.vehicle.getSpeed(vehicle_id) < _WAVE_QUEUE_SPEED
                ]
            for vehicle_id in vehicles:
                # Not police cars, and not ambulances: they already get
                # green signals, and ignoring right of way causes crashes.
                if (vehicle_id in self._waved or vehicle_id.startswith("police")
                        or vehicle_id.startswith("ambulance")):
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

    def _release_lane(self, lane_id):
        self._held_lanes.discard(lane_id)
        holders = SumoResponder._holders.get(lane_id, set())
        holders.discard(id(self))
        if holders:
            return  # another responder still holds it
        SumoResponder._holders.pop(lane_id, None)
        speed = SumoResponder._original_limits.pop(lane_id, None)
        if speed is None:
            return
        try:
            traci.lane.setMaxSpeed(lane_id, speed)
        except traci.TraCIException:
            pass

    def release_junctions(self):
        for lane_id in list(self._held_lanes):
            self._release_lane(lane_id)
        present = set(traci.vehicle.getIDList())
        for vehicle_id in self._waved & present:
            try:
                traci.vehicle.setSpeedMode(vehicle_id, _NORMAL_SPEED_MODE)
            except traci.TraCIException:
                pass
        self._held_lanes.clear()
        self._waved.clear()


# ---------------------------------------------------------
# Drivers giving way to the siren
# ---------------------------------------------------------

# Drivers this far ahead of a siren, in its path, may make room (m).
SIREN_RANGE_METERS = 100

# Share of drivers who react to the siren (not everyone notices or
# cares), and how long after first hearing it they act (s).
GIVE_WAY_COMPLIANCE = 0.75
GIVE_WAY_REACTION_SECONDS = 2

# At most this many vehicles ahead are looked at per siren and step.
_MAX_AHEAD = 20

# Moving slower than this, a driver is stuck in a queue (m/s).
_QUEUE_SPEED = 1.0

# Roads wide enough to pull over to the side: main roads usually have
# a shoulder or kerb space; narrow residential lanes do not. (Every car
# lane in the network is 3.2 m wide and there are no sidewalks, so the
# road type is the best sign of room at the roadside.)
_PULL_OVER_ROAD_TYPES = ("trunk", "primary", "secondary", "tertiary")

# No pulling over this close to the junction ahead: there is no room
# at the stop line (m).
_JUNCTION_CLEARANCE_METERS = 20

# A driver who pulled over rejoins once the siren vehicle has passed,
# and after this long at the latest (s, fail-safe).
_PULL_OVER_SECONDS = 60

# Drivers stuck inside a junction this close ahead of a siren push on
# into any gap (ignoring right of way, keeping a safe distance), like
# the police wave-through: a blocked junction box is what holds the
# siren vehicle up even when its signal is green (m).
_JUNCTION_BOX_METERS = 50


class SumoGiveWay:
    """
    Drivers ahead of the ambulance (and of police cars on their way to
    a jam) make room when they hear the siren, as the law requires in
    India (Motor Vehicles Act, section 194E):

        moving, road with 2+ lanes   change to another lane (if SUMO
                                     finds a gap)
        stuck in a queue on a main   pull over to the roadside (a short
        road, not near the junction  parking stop off the lane), rejoin
                                     once the siren vehicle has passed
        stuck inside a junction      push on into any gap (ignoring right
        just ahead                   of way, keeping a safe distance)
        otherwise                    stay (no room: police clear jams)

    Only GIVE_WAY_COMPLIANCE of drivers react (always the same ones, so
    runs are repeatable), GIVE_WAY_REACTION_SECONDS after first hearing
    the siren. Only vehicles directly in the siren vehicle's path (its
    leaders, also on the next roads of its route) are asked; not
    vehicles inside a junction, police cars or crashed vehicles.
    """

    def __init__(self):
        self.pulled_over = {}   # vehicle id -> (siren id, since)
        self._heard = {}        # vehicle id -> when it first heard a siren
        self._changed = set()   # vehicles that changed lane for a siren
        self._ignored = set()   # drivers who did not react
        self._pushing = set()   # pushing out of a junction box
        self.lane_changes = 0
        self.pull_overs = 0
        self.junction_pushes = 0

    def step(self, ambulance_ids, now):
        """Once per simulation step: drivers ahead of the ambulances (one
        id or a list) and of every police car (siren on) make room.
        Returns how many drivers made room this step."""
        if isinstance(ambulance_ids, str):
            ambulance_ids = [ambulance_ids]
        present = set(traci.vehicle.getIDList())
        self._rejoin(present, now)
        self._end_pushes(present)
        sirens = list(ambulance_ids) + sorted(
            vehicle_id for vehicle_id in present if vehicle_id.startswith("police")
        )
        made_room = 0
        for siren_id in sirens:
            if siren_id in present:
                made_room += self._clear_path(siren_id, now)
                made_room += self._clear_junction_box(siren_id)
        return made_room

    def status(self):
        return {
            "lane_changes": self.lane_changes,
            "pull_overs": self.pull_overs,
            "junction_pushes": self.junction_pushes,
            "pulled_over_now": len(self.pulled_over),
            "did_not_react": len(self._ignored),
        }

    def release_all(self):
        """The trip ended: everyone still pulled over drives on, and
        everyone pushing out of a junction drives normally again."""
        present = set(traci.vehicle.getIDList())
        for vehicle_id in list(self.pulled_over):
            if vehicle_id in present:
                self._drive_on(vehicle_id)
        self.pulled_over.clear()
        for vehicle_id in self._pushing & present:
            try:
                traci.vehicle.setSpeedMode(vehicle_id, _NORMAL_SPEED_MODE)
            except traci.TraCIException:
                pass
        self._pushing.clear()

    def _clear_junction_box(self, siren_id):
        """Drivers standing still inside the junctions within
        _JUNCTION_BOX_METERS ahead on the siren vehicle's route push on
        into any gap."""
        from simulation.sumo.route_planner import _net

        try:
            route = traci.vehicle.getRoute(siren_id)
            index = traci.vehicle.getRouteIndex(siren_id)
            road = traci.vehicle.getRoadID(siren_id)
            position = traci.vehicle.getLanePosition(siren_id)
        except traci.TraCIException:
            return 0
        if index < 0:
            return 0
        net = _net()

        # Junctions ahead within range: the end of the current road, then
        # the ends of the next roads while still in range.
        nodes = []
        distance = 0.0
        for offset, road_id in enumerate(route[index:]):
            try:
                edge = net.getEdge(road_id)
            except KeyError:
                break
            if offset == 0:
                left = edge.getLength() - position if road == road_id else 0.0
            else:
                left = edge.getLength()
            distance += max(left, 0.0)
            if distance > _JUNCTION_BOX_METERS:
                break
            nodes.append(edge.getToNode())

        pushed = 0
        for node in nodes:
            # The junction's inside: the "via" lanes of its connections
            # (as SumoResponder.control_junctions finds them).
            boxes = {
                connection.getViaLaneID().rsplit("_", 1)[0]
                for edge in node.getIncoming()
                for connections in edge.getOutgoing().values()
                for connection in connections
                if connection.getViaLaneID()
            }
            for box in boxes:
                try:
                    vehicles = traci.edge.getLastStepVehicleIDs(box)
                except traci.TraCIException:
                    continue
                for vehicle_id in vehicles:
                    if (
                        vehicle_id in self._pushing
                        or vehicle_id.startswith(("police", "incident_", "ambulance"))
                        or not self._reacts(vehicle_id)
                    ):
                        continue
                    try:
                        if traci.vehicle.getSpeed(vehicle_id) >= _QUEUE_SPEED:
                            continue  # moving: not stuck
                        traci.vehicle.setSpeedMode(vehicle_id, _WAVED_THROUGH_SPEED_MODE)
                    except traci.TraCIException:
                        continue
                    self._pushing.add(vehicle_id)
                    self.junction_pushes += 1
                    pushed += 1
        return pushed

    def _end_pushes(self, present):
        """Out of the junction: drive normally again."""
        for vehicle_id in list(self._pushing):
            if vehicle_id not in present:
                self._pushing.discard(vehicle_id)
                continue
            try:
                if traci.vehicle.getRoadID(vehicle_id).startswith(":"):
                    continue
                traci.vehicle.setSpeedMode(vehicle_id, _NORMAL_SPEED_MODE)
            except traci.TraCIException:
                pass
            self._pushing.discard(vehicle_id)

    @staticmethod
    def _reacts(vehicle_id):
        """The same drivers react every run (repeatable results)."""
        return zlib.crc32(vehicle_id.encode()) % 100 < GIVE_WAY_COMPLIANCE * 100

    def _clear_path(self, siren_id, now):
        made_room = 0
        current, covered = siren_id, 0.0
        for _ in range(_MAX_AHEAD):
            try:
                leader = traci.vehicle.getLeader(current, SIREN_RANGE_METERS - covered)
            except traci.TraCIException:
                break
            if not leader or not leader[0]:
                break
            vehicle_id, gap = leader
            try:
                covered += max(gap, 0.0) + traci.vehicle.getLength(vehicle_id)
            except traci.TraCIException:
                break
            current = vehicle_id
            if covered > SIREN_RANGE_METERS + 10:
                break
            if (
                vehicle_id in self.pulled_over
                or vehicle_id.startswith(("police", "incident_", "ambulance"))
            ):
                continue
            if not self._reacts(vehicle_id):
                self._ignored.add(vehicle_id)
                continue
            heard = self._heard.setdefault(vehicle_id, now)
            if now - heard < GIVE_WAY_REACTION_SECONDS:
                continue
            if self._make_room(vehicle_id, siren_id, now):
                made_room += 1
        return made_room

    def _make_room(self, vehicle_id, siren_id, now):
        try:
            lane_id = traci.vehicle.getLaneID(vehicle_id)
            if not lane_id or lane_id.startswith(":"):
                return False  # inside a junction: the police wave it through
            road_id = traci.vehicle.getRoadID(vehicle_id)
            lane_index = traci.vehicle.getLaneIndex(vehicle_id)
            lanes = traci.edge.getLaneNumber(road_id)
            speed = traci.vehicle.getSpeed(vehicle_id)
        except traci.TraCIException:
            return False

        if speed < _QUEUE_SPEED and self._pull_over(
            vehicle_id, road_id, lane_id, lane_index, speed, siren_id, now
        ):
            return True
        if lanes > 1:
            return self._change_lane(vehicle_id, road_id, lane_index, lanes)
        return False

    def _change_lane(self, vehicle_id, road_id, lane_index, lanes):
        # Towards the roadside (lane 0), or away from it when already there.
        target = lane_index - 1 if lane_index > 0 else lane_index + 1
        if target >= lanes or not self._lane_allowed(f"{road_id}_{target}"):
            return False
        try:
            traci.vehicle.changeLane(vehicle_id, target, 10.0)
        except traci.TraCIException:
            return False
        if vehicle_id in self._changed:
            return False  # asked before: not counted again
        self._changed.add(vehicle_id)
        self.lane_changes += 1
        return True

    def _pull_over(self, vehicle_id, road_id, lane_id, lane_index, speed, siren_id, now):
        from simulation.sumo.route_planner import _net

        try:
            road_type = _net().getEdge(road_id).getType() or ""
        except KeyError:
            return False
        if not any(f"highway.{kind}" in road_type for kind in _PULL_OVER_ROAD_TYPES):
            return False  # narrow street: no room at the side
        try:
            position = traci.vehicle.getLanePosition(vehicle_id)
            decel = traci.vehicle.getDecel(vehicle_id)
            length = traci.lane.getLength(lane_id)
        except traci.TraCIException:
            return False
        braking = speed * speed / (2 * decel) if decel > 0 else 0.0
        stop_at = position + braking + 1.0
        if stop_at > length - _JUNCTION_CLEARANCE_METERS:
            return False  # at the junction: no room at the stop line
        try:
            traci.vehicle.setStop(
                vehicle_id, road_id, pos=stop_at, laneIndex=lane_index,
                duration=_PULL_OVER_SECONDS, flags=tc.STOP_PARKING,
            )
        except traci.TraCIException:
            return False
        self.pulled_over[vehicle_id] = (siren_id, now)
        self.pull_overs += 1
        return True

    def _rejoin(self, present, now):
        for vehicle_id, (siren_id, since) in list(self.pulled_over.items()):
            if vehicle_id not in present:
                del self.pulled_over[vehicle_id]  # left the simulation
                continue
            if now - since >= _PULL_OVER_SECONDS or self._passed(siren_id, vehicle_id, present):
                self._drive_on(vehicle_id)
                del self.pulled_over[vehicle_id]

    def _passed(self, siren_id, vehicle_id, present):
        """True once the siren vehicle is past the driver (or gone)."""
        if siren_id not in present:
            return True
        try:
            distance = traci.vehicle.getDrivingDistance(
                siren_id,
                traci.vehicle.getRoadID(vehicle_id),
                traci.vehicle.getLanePosition(vehicle_id),
            )
        except traci.TraCIException:
            return True
        # Negative, or SUMO's "not on the route ahead" value: passed.
        return distance < 0

    def _drive_on(self, vehicle_id):
        try:
            if traci.vehicle.isStoppedParking(vehicle_id):
                traci.vehicle.resume(vehicle_id)
            elif traci.vehicle.getStops(vehicle_id, 1):
                # Still on the way to its stop: cancel the stop.
                traci.vehicle.replaceStop(vehicle_id, 0, "")
            # else: the stop is already over.
        except traci.TraCIException:
            pass

    @staticmethod
    def _lane_allowed(lane_id):
        try:
            allowed = traci.lane.getAllowed(lane_id)
            disallowed = traci.lane.getDisallowed(lane_id)
        except traci.TraCIException:
            return False
        if allowed:
            return "passenger" in allowed
        return "passenger" not in disallowed


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

    def active_roads(self):
        """Roads still blocked by an accident (not yet cleared)."""
        return {
            incident["road_id"]
            for incident in _INCIDENTS.values()
            if incident["cleared_at"] is None
        }

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
