import math

import certifi #this gives Python a trusted collection of SSL certificates.(Python, when connecting securely to the internet, use these trusted certificates.)
import requests  #requests lets Python communicate with websites/APIs.

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# INTERSECTION EXTRACTION (Take the route information returned by OSRM and pull out all the intersection points.)
def extract_intersections(legs):
    intersections = []   #collect all the intersections inside it.

    #For every leg in the route, process it
    for leg in legs:
        #Go through every step one by one.
        for step in leg["steps"]: 
            #Give me the intersections(i.e. intersection information) belonging to this step.   
            for intersection in step.get(
                "intersections",
                [],
            ):
                location = intersection.get("location")     
                bearings = intersection.get("bearings",[],)   #Bearings tell us the directions/approaches associated with the intersection. i.e. East , West , South , North

                if location:
                    intersections.append(
                        {
                            "longitude": location[0],
                            "latitude": location[1],
                            "road_count": len(bearings),
                        }
                    )

    return intersections


def calculate_distance(
    latitude1,
    longitude1,
    latitude2,
    longitude2,
):
    earth_radius = 6371000

    #convert degrees to radians 
    lat1 = math.radians(latitude1)
    lat2 = math.radians(latitude2)

    #Find the difference between locations
    delta_lat = math.radians(latitude2 - latitude1)
    delta_lon = math.radians(longitude2 - longitude1)

    #Haversine formula.(Calculate the distance between two points on Earth's curved surface.)
    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1)
        * math.cos(lat2)
        * math.sin(delta_lon / 2) ** 2
    )

    #calculate the angle (It converts the previous calculation into the amount of Earth's surface between the two points.)
    c = 2 * math.atan2(
        math.sqrt(a),
        math.sqrt(1 - a),
    )

    return earth_radius * c  #i.e Distance = Earth's radius × angular distance

# CLUSTER INTERSECTIONS INTO JUNCTIONS
def cluster_intersections(
    intersections,
    threshold_meters=30,
):
    junctions = []

    for intersection in intersections:

        #Do we currently have zero junctions?...so below returns true and above junction =[] returns false 
        if not junctions:
            #create first junction
            junctions.append(
                {
                    "latitude": intersection["latitude"],
                    "longitude": intersection["longitude"],
                    "points": [intersection],
                }
            )

            continue

        #Has this intersection already been added to an existing junction?
        added_to_junction = False

        for junction in junctions:
            #How far is this intersection from this existing junction? that's why calculate the distance 
            distance = calculate_distance(
                intersection["latitude"],
                intersection["longitude"],
                junction["latitude"],
                junction["longitude"],
            )
            #If the distance is within 30m:
            if distance <= threshold_meters:
                #Add the intersection to the junction
                junction["points"].append(intersection)

                added_to_junction = True
                break

        #This intersection doesn't belong to any existing junction.
        if not added_to_junction:
            #Create a New Junction 
            junctions.append(
                {
                    "latitude": intersection["latitude"],
                    "longitude": intersection["longitude"],
                    "points": [intersection],
                }
            )

    return junctions

#Take our 18 candidate junctions → ask OpenStreetMap where the traffic signals are → return their GPS coordinates.
def get_traffic_signals(junctions):
    #check if there are no junctions 
    if not junctions:
        return []

    #Take the latitude from every junction and put them into a list
    latitudes = [
        junction["latitude"]
        for junction in junctions
    ]

    #Take the longitude from every junction and put them into a list
    longitudes = [
        junction["longitude"]
        for junction in junctions
    ]

    #create a rectangle around our route area. below are 4 boundaries and together they create a bounding box 
    #instead of Searching the whole world for traffic signals , Only search inside this rectangle.
    south = min(latitudes)   #bottom side of our rectangle map
    north = max(latitudes)   #top side of our rectangle map
    west = min(longitudes)   #left side of our rectangle map
    east = max(longitudes)   #right side of our rectangle map

    # add a small extra area around the bounding box.
    # 0.001 degree is approximately 100 meters in latitude.
    buffer = 0.001

    south -= buffer  #souther boundary moves outward 
    north += buffer  #northern boundary moves outward
    west -= buffer   #western boundary moves outward 
    east += buffer   #eastern boundary moves outward 

    # Overpass query asking database "Give me all traffic-signal nodes inside this rectangle"
    query = f"""
        [out:json][timeout:25]; 
        
        node
            [highway=traffic_signals]
            ({south},{west},{north},{east});

        out;
    """

    #preparing information to send along with the HTTP request.
    headers = {
        #User-Agent It tells the server: "Who is making this request?"
        "User-Agent": (
            "EmergencyCorridorPOC/1.0 "
            "(Python requests)"
        )
    }

    print("Querying OpenStreetMap traffic signals...")

    #handle network issues 
    try:
        #use post request to send our Overpass Query 
        response = requests.post(
            OVERPASS_URL,   #Where should I send the request?
            data=query,     #this sends our overpass query 
            headers=headers,  #sends our User-Agent 
            timeout=35,
            verify=certifi.where(),
        )

        #check response status 
        print("Overpass status:",response.status_code,)

        if response.status_code != 200:
            print(
                "Warning: OpenStreetMap traffic signal "
                "service is currently unavailable."
            )
            return []

        #convert response(i.e json format) into python data (i.e. dictionary and list )
        data = response.json()

    #handle network errors 
    except requests.RequestException as error:
        print("Warning: Could not retrieve traffic signals:",error,)

        return []

    #prepare a empty list where we'll store the signals we receive.
    traffic_signals = []

    for element in data.get("elements", []):
        traffic_signals.append(
            {
                "latitude": element["lat"],
                "longitude": element["lon"],
            }
        )

    #print number of signals we received in the routing area bounding box 
    print("Traffic signals found:",len(traffic_signals),)

    return traffic_signals   #e.g it returns 14 signals recived within that routing area

#It does not yet decide which of those 14 actually belong to our 18 candidate junctions.


# MATCH TRAFFIC SIGNALS WITH CANDIDATE JUNCTIONS(i.e Of those 14 traffic signals, which one is close enough to each candidate junction?)
def match_traffic_signals(
    junctions,    #these are the candidate junctions 
    traffic_signals,  #signals we got from openstreet 
    threshold_meters=30,
):
    #creating an empty list to store our final results.
    matched_junctions = []

    for junction in junctions:

        matched_signal = None
        nearest_distance = None

        for signal in traffic_signals:

            #How far is this traffic signal from this candidate junction?
            distance = calculate_distance(
                junction["latitude"],
                junction["longitude"],
                signal["latitude"],
                signal["longitude"],
            )

            if distance <= threshold_meters:

                if (
                    nearest_distance is None
                    or distance < nearest_distance
                ):
                    nearest_distance = distance
                    matched_signal = signal

        #create final junction object
        matched_junction = {
            "latitude": junction["latitude"],
            "longitude": junction["longitude"],
            "points": junction["points"],
            "traffic_signal": matched_signal,
        }

        matched_junctions.append(
            matched_junction
        )

    return matched_junctions