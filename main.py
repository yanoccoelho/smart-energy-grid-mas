# main.py
import asyncio
import spade
from config import XMPP_SERVER
from agents.household_agent import HouseholdAgent
from agents.producer_agent import ProducerAgent
from agents.grid_node_agent import GridNodeAgent
from agents.storage_manager_agent import StorageManagerAgent


async def main():
    """
    SMART ENERGY GRID - Multi-Agent Simulation
    ------------------------------------------------
    Decentralized energy market with:
      - Multiple consumers (households)
      - Prosumers (with production capacity)
      - Renewable producers (solar + wind)
      - Storage manager (battery system)
      - Grid node (market coordinator)

    Agents communicate via XMPP and participate
    in energy auctions coordinated by the Grid Node.
    """

    print("Starting simulation setup...")

    # GRID NODE
    grid_node_jid = f"grid_node1@{XMPP_SERVER}"
    grid_node_agent = GridNodeAgent(grid_node_jid, "password123")

    # HOUSEHOLDS
    # Number of pure consumers and prosumers
    num_consumers = 5
    num_prosumers = 2

    # Generate multiple consumers automatically
    consumers = [
        HouseholdAgent(
            f"consumer{i+1}@{XMPP_SERVER}",
            "password123",
            grid_node_jid,
            is_prosumer=False,
            price_max=0.27
        )
        for i in range(num_consumers)
    ]

    # Generate multiple prosumers automatically
    prosumers = [
        HouseholdAgent(
            f"prosumer{i+1}@{XMPP_SERVER}",
            "password123",
            grid_node_jid,
            is_prosumer=True,
            price_max=0.26,
            ask_price=0.21
        )
        for i in range(num_prosumers)
    ]

    # PRODUCERS
    solar_farm = ProducerAgent(
        f"solarfarm1@{XMPP_SERVER}",
        "password123",
        grid_node_jid,
        production_type="solar",
        max_capacity_kw=250.0,
        ask_price=0.18
    )

    wind_turbine = ProducerAgent(
        f"windturbine1@{XMPP_SERVER}",
        "password123",
        grid_node_jid,
        production_type="eolic",
        max_capacity_kw=200.0,
        ask_price=0.19
    )

    # STORAGE
    storage_mgr = StorageManagerAgent(
        f"storage1@{XMPP_SERVER}",
        "password123",
        grid_node_jid,
        soc_init_frac=0.5,
        capacity_kwh=30.0,
        ask_price=0.22,
        price_max=0.28
    )

    # AGENT REGISTRY
    # Assign unique web ports sequentially
    agents = [("grid_node", grid_node_agent, 10000)]

    # Register all households (consumers + prosumers)
    port = 10001
    for i, consumer in enumerate(consumers, start=1):
        agents.append((f"consumer{i}", consumer, port))
        port += 1

    for i, prosumer in enumerate(prosumers, start=1):
        agents.append((f"prosumer{i}", prosumer, port))
        port += 1

    # Add producers and storage
    agents.extend([
        ("solarfarm1", solar_farm, port),
        ("windturbine1", wind_turbine, port + 1),
        ("storage1", storage_mgr, port + 2),
    ])

    # STARTUP SEQUENCE
    for name, agent, port in agents:
        await agent.start(auto_register=True)
        agent.web.start(hostname="127.0.0.1", port=port)
        print(f"{name} started (web UI: http://127.0.0.1:{port})")

    print(f"\nSimulation running with {num_consumers} consumers, {num_prosumers} prosumers, 2 producers and 1 storage.\n")

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("Stopping all agents...")
    finally:
        for _, agent, _ in agents:
            try:
                agent.web.stop()
            except Exception:
                pass
            await agent.stop()
        print("Simulation finished.")


if __name__ == "__main__":
    spade.run(main())
