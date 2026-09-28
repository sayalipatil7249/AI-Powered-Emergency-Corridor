import sumolib

NET_FILE = "simulation/sumo/pune_network_v2/osm.net.xml.gz"
ROUTE_FILE = "simulation/sumo/pune_network_v2/ambulance.rou.xml"

net = sumolib.net.readNet(NET_FILE)

vehicle_route = sumolib.xml.parse(
    ROUTE_FILE,
    "vehicle"
)

for vehicle in vehicle_route:
    edges = vehicle.route[0].edges.split()

    print("\nAmbulance route:")
    print(f"Total edges: {len(edges)}")

    signal_ids = []

    for edge_id in edges:
        edge = net.getEdge(edge_id)

        tls = edge.getTLS()

        if tls is not None:
            tls_id = tls.getID()

            if tls_id not in signal_ids:
                signal_ids.append(tls_id)

    print("\nTraffic lights encountered:")

    for index, signal_id in enumerate(signal_ids, start=1):
        print(f"{index}. {signal_id}")

    print(f"\nTotal traffic-light controllers: {len(signal_ids)}")