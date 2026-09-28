#corridor_service.py takes those signal ETAs and answers:
#"Which traffic signal should be ACTIVE, which should be PREPARING, which should be STANDBY, and which should remain NORMAL?"


def build_emergency_corridor(
        signal_etas,   #This is the output from our ETA service.
        ambulance_route_distance=0,   #initially , The ambulance hasn't travelled any distance on this route yet.
):
    #We're going to put our upcoming junctions into this empty list.
    corridor = []

    #Find upcoming signals (Take only those signals that the ambulance has not crossed yet)
    upcoming_signals = [
        signal
        for signal  in signal_etas
        if signal["route_distance_meters"] > ambulance_route_distance  #Only consider signals that the ambulance has not crossed yet.
    ]

    #loop through upcoming signals 
    for index, signal in enumerate(upcoming_signals):
        if index == 0:
            state = "ACTIVE"
        elif index == 1:
            state = "PREPARING"
        elif index == 2:
            state = "STANDBY"
        else:
            state = "NORMAL"

        corridor.append({
            **signal,
            "state": state,
        })

    return corridor