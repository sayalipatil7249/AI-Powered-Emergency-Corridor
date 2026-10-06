#Traffic Signal State Machine (Handles the traffic signal states:)
def update_signal_states(signal_etas,ambulance_route_distance,):
    updated_signals = []

    #Among the upcoming signals, which one am I currently processing?
    upcoming_index = 0    #0 means first upcoming signal 

    #loop thr every signal 
    for signal in signal_etas:
        #get signal distance 
        signal_distance = signal["route_distance_meters"]

        #Check if ambulance already crossed it
        if signal_distance <= ambulance_route_distance:
            state = "NORMAL"
        else:
            if upcoming_index == 0:
                state = "ACTIVE"
            elif upcoming_index == 1:
                state = "PREPARING"
            elif upcoming_index == 2:
                state = "STANDBY"
            else:
                state = "NORMAL"

            upcoming_index += 1

        #add state to signal 
        updated_signals.append({
            **signal,
            "state": state,
        })

    return updated_signals