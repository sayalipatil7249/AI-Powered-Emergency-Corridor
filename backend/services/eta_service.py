from backend.services.junction_service import calculate_distance


# 1. Find the nearest OSRM route point from the traffic signal
def find_nearest_route_point(
    signal_latitude,
    signal_longitude,
    route_coordinates,  #this contians all points on OSRM route
):
    """
    Find the OSRM route point closest to the traffic signal.
    """

    nearest_point = None
    nearest_distance = None

    for coordinate in route_coordinates:

        route_longitude = coordinate[0]
        route_latitude = coordinate[1]

        distance = calculate_distance(
            signal_latitude,
            signal_longitude,
            route_latitude,
            route_longitude,
        )

        if (
            nearest_distance is None
            or distance < nearest_distance
        ):
            nearest_distance = distance
            nearest_point = coordinate

    return nearest_point, nearest_distance


# 2. Calculate road distance from the beginning of the route
#    to the signal's nearest route point
def calculate_route_distance_to_point(
    route_coordinates,
    target_point,
):
    """
    Calculate the road distance from the beginning
    of the OSRM route to the target point.(target point means nearest point to signal)
    """

    target_longitude = target_point[0]
    target_latitude = target_point[1]

    nearest_index = None
    nearest_distance = None

    # Find the route point closest to the target
    for index, coordinate in enumerate(route_coordinates):  #enumerate() indicates give me the point and its index number 

        route_longitude = coordinate[0]
        route_latitude = coordinate[1]

        distance = calculate_distance(
            target_latitude,
            target_longitude,
            route_latitude,
            route_longitude,
        )

        if (
            nearest_distance is None
            or distance < nearest_distance
        ):
            nearest_distance = distance
            nearest_index = index

    # Add the distance of every route segment
    # from route start to the target point
    total_distance = 0

    for index in range(nearest_index):

        point1 = route_coordinates[index]
        point2 = route_coordinates[index + 1]

        segment_distance = calculate_distance(
            point1[1],
            point1[0],
            point2[1],
            point2[0],
        )

        total_distance += segment_distance

    return total_distance

#3.Calculate the ambulance route position 
# Calculate how far the ambulance has travelled along the OSRM route
#Ambulance → nearest OSRM route point → distance from route start   
def calculate_ambulance_route_position(
    ambulance_latitude,
    ambulance_longitude,
    route_coordinates,
):
    """
    Calculate how far the ambulance has travelled
    along the selected OSRM route.
    """

    nearest_point = None
    nearest_index = None
    nearest_distance = None

    # Find the route point closest to the ambulance
    for index, coordinate in enumerate(route_coordinates):

        route_longitude = coordinate[0]
        route_latitude = coordinate[1]

        distance = calculate_distance(
            ambulance_latitude,
            ambulance_longitude,
            route_latitude,
            route_longitude,
        )

        if (
            nearest_distance is None
            or distance < nearest_distance
        ):
            nearest_distance = distance
            nearest_point = coordinate
            nearest_index = index

    # Calculate road distance from route start
    travelled_distance = 0

    for index in range(nearest_index):
        point1 = route_coordinates[index]
        point2 = route_coordinates[index + 1]

        segment_distance = calculate_distance(
            point1[1],
            point1[0],
            point2[1],
            point2[0],
        )

        travelled_distance += segment_distance

    return {
        "travelled_distance_meters": round(travelled_distance,2,),
        "nearest_route_point": nearest_point,
        "nearest_route_index": nearest_index,
    }


# 4. Calculate ETA to a traffic signal
#this calculates the initial eta
def calculate_eta(
    distance_to_signal, #distance from start of the route to the signal  e.g 925.32 m
    total_route_distance, #complete distance from ambulance to hospital  e.g. 1929.6 m 
    total_route_duration, #complete route duration to complete this total distance e.g. 214.1 sec 
):
    """
    Estimate the ambulance's ETA to a traffic signal.
    """

    if total_route_distance <= 0:
        return 0

    eta = (distance_to_signal/ total_route_distance) * total_route_duration   #i.e.eta = (925.32 / 1929.6) * 214.1


    return round(eta, 2)


#5. Calculate the dynamic ETA ...means as the ambulance moves it calculates the remaining distance and time 
#this calculate moving ambulance ETA
def calculate_dynamic_eta(
    signal_route_distance,
    ambulance_route_distance,
    total_route_distance,
    total_route_duration,
):
    """
    Calculate ETA to a signal from the ambulance's
    current position.
    """

    remaining_distance = (signal_route_distance - ambulance_route_distance)

    if remaining_distance <= 0:
        return 0

    remaining_route_distance = (total_route_distance- ambulance_route_distance)

    if remaining_route_distance <= 0:
        return 0

    remaining_route_duration = (remaining_route_distance / total_route_distance) * total_route_duration

    eta = ( remaining_distance / remaining_route_distance ) * remaining_route_duration

    return round(eta, 2)



# 6. Calculate ETA for every traffic signal
#this function calculates the initial ETA
def calculate_signal_etas(
    junctions,
    route_coordinates,
    total_route_distance,
    total_route_duration,
):
    """
    Calculate road distance and ETA for every
    traffic signal present on the route.
    """

    signals = []

    for junction in junctions:

        signal = junction.get("traffic_signal")

        # Ignore junctions without traffic signals
        if signal is None:
            continue

        signal_latitude = signal["latitude"]
        signal_longitude = signal["longitude"]

        # Find nearest point on the OSRM route
        nearest_point, nearest_distance = (
            find_nearest_route_point(
                signal_latitude,
                signal_longitude,
                route_coordinates,
            )
        )

        if nearest_point is None:
            continue

        # Calculate road distance from route start
        road_distance = calculate_route_distance_to_point(
            route_coordinates,
            nearest_point,
        )

        # Calculate ETA
        eta = calculate_eta(
            distance_to_signal=road_distance,
            total_route_distance=total_route_distance,
            total_route_duration=total_route_duration,
        )

        signals.append(
            {
                "latitude": signal_latitude,
                "longitude": signal_longitude,
                "route_distance_meters": round(
                    road_distance,
                    2,
                ),
                "eta_seconds": round(
                    eta,
                    2,
                ),
            }
        )

    # Arrange signals according to their position on the route
    # Sort signals according to their position on the ambulance route ...Arrange the traffic signals according to how far they are from the ambulance's starting point.
    signals.sort(
        key=lambda signal:
        signal["route_distance_meters"]
    )

    # Give human-friendly signal numbers
    for index, signal in enumerate(
        signals,
        start=1,  #start=1 becuse humans normally count signals from 1 not 0 i.e. signal 1 , signal 2 , signal 3 , etc 
    ):
        signal["signal_number"] = index

    return signals


#7.this function calculates ETA from the ambulance's current position.
def calculate_dynamic_signal_etas(
    signal_etas,
    ambulance_route_distance,
    total_route_distance,
    total_route_duration,
):
    """
    Recalculate ETA to every signal from the ambulance's
    current position on the saved route.
    """

    dynamic_signals = []

    for signal in signal_etas:

        eta = calculate_dynamic_eta(
            signal_route_distance=signal["route_distance_meters"],
            ambulance_route_distance=ambulance_route_distance,
            total_route_distance=total_route_distance,
            total_route_duration=total_route_duration,
        )

        dynamic_signals.append({
            **signal,
            "eta_seconds": eta,
        })

    return dynamic_signals