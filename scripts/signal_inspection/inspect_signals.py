import os
import traci


SUMO_BINARY = os.path.join(os.environ["SUMO_HOME"], "bin", "sumo")

NET_FILE = "simulation/sumo/pune_network_v2/osm.net.xml.gz"
NORMAL_TRAFFIC = "simulation/sumo/pune_network_v2/normal_traffic.rou.xml"
AMBULANCE_ROUTE = "simulation/sumo/pune_network_v2/ambulance.rou.xml"


SIGNALS = [
    "672160723",
    "cluster_11459261068_11459269187_11459269431_672160725",
    "cluster_11459329985_11459329986_11459329987_245646882",
]


sumo_command = [
    SUMO_BINARY,
    "--net-file",
    NET_FILE,
    "--route-files",
    f"{NORMAL_TRAFFIC},{AMBULANCE_ROUTE}",
]

traci.start(sumo_command)

print("\nConnected to SUMO.")
print("\nTraffic Signal Information")
print("==========================")

for signal_id in SIGNALS:

    print(f"\nSignal: {signal_id}")

    program_id = traci.trafficlight.getProgram(signal_id)
    phase_index = traci.trafficlight.getPhase(signal_id)
    phase_duration = traci.trafficlight.getPhaseDuration(signal_id)
    phase_count = len(traci.trafficlight.getAllProgramLogics(signal_id)[0].phases)

    print(f"Program: {program_id}")
    print(f"Current phase: {phase_index}")
    print(f"Current phase duration: {phase_duration:.1f} seconds")
    print(f"Number of phases: {phase_count}")

    print("Phase definitions:")

    programs = traci.trafficlight.getAllProgramLogics(signal_id)

    for program in programs:

        print(f"  Program ID: {program.programID}")

        for index, phase in enumerate(program.phases):

            print(
                f"    Phase {index}: "
                f"duration={phase.duration:.1f}s, "
                f"state={phase.state}"
            )


traci.close()

print("\nSUMO simulation closed.")