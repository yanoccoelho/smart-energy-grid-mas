# main.py
import asyncio
import spade

from config import XMPP_SERVER
from agents.household_agent import HouseholdAgent
from agents.producer_agent import ProducerAgent
from agents.grid_node_agent import GridNodeAgent
from agents.storage_manager_agent import StorageManagerAgent

async def main():
    print("Starting simulation setup...")

    grid_node_jid = f"grid_node1@{XMPP_SERVER}"
    grid_node_agent = GridNodeAgent(grid_node_jid, "password123")

    # Households
    household_consumer = HouseholdAgent(f"consumer1@{XMPP_SERVER}", "password123", grid_node_jid, is_prosumer=False, price_max=0.27, ask_price=0.00)
    household_prosumer = HouseholdAgent(f"prosumer1@{XMPP_SERVER}", "password123", grid_node_jid, is_prosumer=True,  price_max=0.26, ask_price=0.21)

    # Producers
    solar_farm = ProducerAgent(f"solarfarm1@{XMPP_SERVER}", "password123", grid_node_jid, production_type="solar", max_capacity_kw=200.0, ask_price=0.18)
    wind_turbine = ProducerAgent(f"windturbine1@{XMPP_SERVER}", "password123", grid_node_jid, production_type="eolic", max_capacity_kw=150.0, ask_price=0.19)

    # Storage
    storage_mgr = StorageManagerAgent(f"storage1@{XMPP_SERVER}", "password123", grid_node_jid, soc_init_frac=0.5, capacity_kwh=15.0, ask_price=0.22, price_max=0.28)

    agents = [
        ("grid", grid_node_agent, 10000),
        ("consumer1", household_consumer, 10001),
        ("prosumer1", household_prosumer, 10002),
        ("solarfarm1", solar_farm, 10003),
        ("windturbine1", wind_turbine, 10004),
        ("storage1", storage_mgr, 10005),
    ]

    for _, agent, port in agents:
        await agent.start(auto_register=True)
        agent.web.start(hostname="127.0.0.1", port=port)

    print("\nSimulation running... Press Ctrl+C to stop.")
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
