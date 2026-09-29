"""
The three connectors between the corridor brain and the outside world.

    AmbulanceTracker   where the ambulance is and where it is going
    TrafficSource      what the roads look like (lengths, speeds, queues)
    SignalController   read and control traffic signals

Today they are implemented on top of the SUMO simulation
(simulation/sumo/adapters.py). For a real deployment they would be
implemented on top of:

    AmbulanceTracker   a GPS app in the ambulance + a map-matched route
    TrafficSource      Google Maps Routes API, city cameras, loop detectors
    SignalController   the city's traffic-signal control centre

Nothing outside the adapters should import traci.

Identifiers:
    road_id    a directed road segment (SUMO "edge"; OSM way segment)
    lane_id    one lane of a road ("<road_id>_<n>")
    signal_id  a traffic-signal controller (one junction)
    link_index which movement through the junction a light controls
"""

from typing import Protocol


class AmbulanceTracker(Protocol):
    vehicle_id: str

    def dispatch(self, route: list, depart_position=None,
                 arrival_position=None) -> None:
        """Send the ambulance off along route (road ids)."""

    def is_on_road(self) -> bool:
        """True while the ambulance is driving (departed, not arrived)."""

    def position(self) -> dict:
        """{"x", "y", "latitude", "longitude"} of the ambulance."""

    def speed(self) -> float:
        """Current speed (m/s)."""

    def heading(self) -> float:
        """Direction of travel, degrees clockwise from north."""

    def acceleration(self) -> float:
        """Current acceleration (m/s²)."""

    def max_speed(self) -> float:
        """Top speed the ambulance can drive (m/s)."""

    def route(self) -> list:
        """Road ids of the whole route, start to hospital."""

    def route_index(self) -> int:
        """Index in route() of the road it is on (or just left)."""

    def road_id(self) -> str:
        """Road it is on; junction-internal ids start with ':'."""

    def lane_id(self) -> str:
        """Lane it is on."""

    def lane_position(self) -> float:
        """Metres driven along the current lane."""

    def driving_distance_to(self, road_id: str, position: float) -> float:
        """Metres along the route to a point; negative if unreachable."""

    def upcoming_signals(self) -> list:
        """Signals ahead: [(signal_id, link_index, distance, state)]."""

    def departure_time(self) -> float:
        """Time the ambulance set off (s)."""

    def waiting_time(self) -> float:
        """Total seconds spent (almost) stopped so far."""


class TrafficSource(Protocol):
    def lane_length(self, lane_id: str) -> float:
        """Length of a lane (m)."""

    def lane_speed_limit(self, lane_id: str) -> float:
        """Speed limit of a lane (m/s)."""

    def lane_shape(self, lane_id: str) -> list:
        """Lane geometry as [(x, y), ...]."""

    def road_travel_time(self, road_id: str) -> float:
        """Current travel time over a road given live traffic (s)."""

    def road_halting_count(self, road_id: str) -> int:
        """Vehicles stopped (queued) on a road right now."""

    def road_vehicle_count(self, road_id: str) -> int:
        """Vehicles on a road right now."""

    def road_mean_speed(self, road_id: str) -> float:
        """Mean speed of vehicles on a road right now (m/s)."""

    def road_lane_count(self, road_id: str) -> int:
        """Number of lanes of a road."""

    def lane_halting_count(self, lane_id: str) -> int:
        """Vehicles stopped on a lane."""

    def lane_vehicle_ids(self, lane_id: str) -> list:
        """Vehicles on a lane."""

    def vehicle_count(self) -> int:
        """Vehicles in the whole area right now."""

    def vehicles(self, exclude: str = "") -> list:
        """All other vehicles, for the map: [{"vehicle_id", "x", "y",
        "latitude", "longitude", "speed", "heading", "road_id"}]."""

    def to_latlon(self, x: float, y: float) -> dict:
        """Convert local x/y to {"latitude", "longitude"}."""

    def snap_to_road(self, latitude: float, longitude: float):
        """Road id of the nearest drivable road, or None."""

    def set_road_speed_limit(self, road_id: str, speed: float) -> None:
        """Change a road's speed limit (m/s), e.g. to match live traffic."""


class SignalController(Protocol):
    def signal_ids(self) -> list:
        """All traffic signals in the area."""

    def controlled_links(self, signal_id: str) -> list:
        """Per link index, the movements it controls:
        [[(incoming_lane, outgoing_lane, via_lane), ...], ...]."""

    def light_states(self, signal_id: str) -> str:
        """Current colour per link index, e.g. "GGrrYY"."""

    def phase(self, signal_id: str) -> int:
        """Current phase number."""

    def program(self, signal_id: str) -> str:
        """Current signal program id."""

    def find_green_phase(self, signal_id: str, link_index: int):
        """Phase number that gives green to link_index, or None."""

    def phase_states(self, signal_id: str) -> list:
        """Colours per link index of every phase of the normal program."""

    def phase_state(self, signal_id: str, phase: int):
        """Colour per link index of a phase of the normal program."""

    def set_light_states(self, signal_id: str, states: str) -> None:
        """Show these colours now (e.g. yellow / all-red while switching)."""

    def hold_phase(self, signal_id: str, phase: int) -> None:
        """Switch to and hold a phase (emergency priority)."""

    def restore_normal(self, signal_id: str, held_phase=None) -> None:
        """Return the signal to its normal program (leaving held_phase
        through its yellow phase)."""

    def name(self, signal_id: str, road_id: str = None):
        """Readable name of the junction (street names), or None."""

    def position(self, signal_id: str):
        """{"latitude", "longitude"} of the junction, or None."""


class Responder(Protocol):
    """Help for a stuck ambulance: re-routing and traffic police."""

    def find_route(self, from_road: str, to_road: str, vehicle_type: str = "ambulance"):
        """Fastest route with live travel times: {"roads", "seconds"} or None."""

    def route_seconds(self, roads: list) -> float:
        """Live travel time along these roads (s)."""

    def reroute_ambulance(self, vehicle_id: str, roads: list) -> bool:
        """Put the ambulance on a new route starting with its current road."""

    def route_around(self, vehicle_id: str, avoid_roads: list):
        """Fastest route from the vehicle's current road to its destination
        with current travel times, avoiding these roads: road ids or None.
        Does not change the vehicle's route."""

    def send_unit(self, unit_id: str, roads: list) -> bool:
        """Send a police vehicle along these roads, now."""

    def unit_state(self, unit_id: str, target_road: str):
        """{"latitude", "longitude", "road_id", "arrived"} of a unit,
        {"pending": True, ...} while it waits to set off, or None once it
        has left the simulation."""

    def remove_unit(self, unit_id: str) -> None:
        """Take a police vehicle out of the simulation."""

    def control_junctions(self, roads: list, keep_roads: list) -> int:
        """Police at work on these roads: stop traffic coming in from other
        roads (keep_roads: the ambulance's route keeps moving) and wave
        stuck vehicles through. Call every step; returns vehicles waved."""

    def release_junctions(self) -> None:
        """Police done: traffic back to normal."""

