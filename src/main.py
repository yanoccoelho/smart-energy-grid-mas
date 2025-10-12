import spade
import asyncio

from .config import XMPP_SERVER
from .agents.household_agent import HouseholdAgent
from .agents.producer_agent import ProducerAgent
from .agents.grid_node_agent import GridNodeAgent

async def main():
    print("Starting simulation setup...")

    grid_node_jid = f"grid_node1@{XMPP_SERVER}"
    
    agents = []

    grid_node_agent = GridNodeAgent(grid_node_jid, "password123")
    agents.append(grid_node_agent)

    household_consumer = HouseholdAgent(f"consumer1@{XMPP_SERVER}", "password123", grid_node_jid, is_prosumer=False)
    agents.append(household_consumer)

    household_prosumer = HouseholdAgent(f"prosumer1@{XMPP_SERVER}", "password123", grid_node_jid, is_prosumer=True)
    agents.append(household_prosumer)

    solar_farm = ProducerAgent(f"solarfarm1@{XMPP_SERVER}", "password123", grid_node_jid, 
                               production_type="solar", max_capacity_kw=200.0)
    agents.append(solar_farm)

    wind_turbine = ProducerAgent(f"windturbine1@{XMPP_SERVER}", "password123", grid_node_jid,
                                 production_type="eolic", max_capacity_kw=150.0)
    agents.append(wind_turbine)
    
    for agent in agents:
        await agent.start(auto_register=True)
    
    print("\nSimulation running... Press Ctrl+C to stop.")
    
    while True:
        try:
            await asyncio.sleep(1)
        except KeyboardInterrupt:
            print("Stopping all agents...")
            break
            
    for agent in agents:
        await agent.stop()
        
    print("Simulation finished.")

if __name__ == "__main__":
    spade.run(main())
