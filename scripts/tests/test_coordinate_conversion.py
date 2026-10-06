from simulation.sumo.sumo_bridge import sumo_to_latlon


def main():

    # Example SUMO position.
    x = 5565.79
    y = 2680.79

    position = sumo_to_latlon(
        x,
        y,
    )

    print("SUMO position:")
    print(f"X: {x}")
    print(f"Y: {y}")

    print()

    print("Geographic position:")
    print(
        f"Latitude: {position['latitude']}"
    )
    print(
        f"Longitude: {position['longitude']}"
    )


if __name__ == "__main__":
    main()