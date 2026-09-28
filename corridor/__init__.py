"""
The emergency-corridor "brain": decides which traffic signals to turn
green for the ambulance. It only talks to the interfaces in
corridor/interfaces.py, never to SUMO directly, so the same code can
later drive real city signals.
"""
