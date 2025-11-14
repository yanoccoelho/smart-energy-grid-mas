import asyncio
import time
import spade
from config import XMPP_SERVER
from agents.household_agent import HouseholdAgent
from agents.producer_agent import ProducerAgent
from agents.grid_node_agent import GridNodeAgent
from agents.storage_manager_agent import StorageManagerAgent
from agents.environment_agent import EnvironmentAgent


async def main():
    """
    SMART ENERGY GRID - Multi-Agent Simulation
    With Emergency Storage System & Producer Failure Simulation
    """
    print("=" * 60)
    print("     SMART ENERGY GRID - Multi-Agent System")
    print("=" * 60)
    print("\n🚀 Starting simulation setup...\n")
    
    start_time = time.time()
    
    num_consumers = 5
    num_prosumers = 2
    
    # Environment JID
    env_jid = f"environment@{XMPP_SERVER}"
    
    # Calculate expected agents
    expected_agents = {
        "households": num_consumers + num_prosumers,
        "producers": 2,
        "storage": 1
    }
    
    # ✅ EXTERNAL GRID - PREÇOS VARIÁVEIS
    external_grid_config = {
        "enabled": True,
        "buy_price_min": 0.10,      # Mínimo que paga por surplus
        "buy_price_max": 0.15,      # Máximo que paga por surplus
        "sell_price_min": 0.25,     # Mínimo que cobra para vender
        "sell_price_max": 0.32,     # Máximo que cobra para vender
        "acceptance_prob": 1.0      # 100% disponível (sem blackout)
    }
    
    # GRID NODE
    grid_node_jid = f"grid_node1@{XMPP_SERVER}"
    grid_node_agent = GridNodeAgent(
        jid=grid_node_jid,
        password="password123",
        expected_agents=expected_agents,
        env_jid=env_jid,
        external_grid_config=external_grid_config
    )
    
    # ENVIRONMENT AGENT
    broadcast_list = (
        [f"consumer{i+1}@{XMPP_SERVER}" for i in range(num_consumers)] +
        [f"prosumer{i+1}@{XMPP_SERVER}" for i in range(num_prosumers)] +
        [f"solarfarm1@{XMPP_SERVER}", f"windturbine1@{XMPP_SERVER}"]
    )
    
    environment_agent = EnvironmentAgent(
        jid=env_jid,
        password="password123",
        broadcast_list=broadcast_list
    )
    
    # CONSUMERS - price_max maior para aceitar external grid
    consumers = [
        HouseholdAgent(
            jid=f"consumer{i+1}@{XMPP_SERVER}",
            password="password123",
            grid_node_jid=grid_node_jid,
            is_prosumer=False,
            price_max=0.35
        )
        for i in range(num_consumers)
    ]
    
    # PROSUMERS
    prosumers = [
        HouseholdAgent(
            jid=f"prosumer{i+1}@{XMPP_SERVER}",
            password="password123",
            grid_node_jid=grid_node_jid,
            is_prosumer=True,
            price_max=0.35,  # ✅ Aumentado para aceitar external grid
            ask_price=0.20
        )
        for i in range(num_prosumers)
    ]
    
    # PRODUCERS
    solar_farm = ProducerAgent(
        jid=f"solarfarm1@{XMPP_SERVER}",
        password="password123",
        grid_node_jid=grid_node_jid,
        production_type="solar",
        max_capacity_kw=20.0,
        ask_price=0.18
    )
    
    wind_turbine = ProducerAgent(
        jid=f"windturbine1@{XMPP_SERVER}",
        password="password123",
        grid_node_jid=grid_node_jid,
        production_type="wind",
        max_capacity_kw=50.0,
        ask_price=0.19
    )
    
    # ✅ STORAGE - 50 kWh, 100% FULL, EMERGENCY ONLY
    storage_mgr = StorageManagerAgent(
        jid=f"storage1@{XMPP_SERVER}",
        password="password123",
        grid_node_jid=grid_node_jid,
        soc_init_frac=1.0,          # 100% charged
        capacity_kwh=50.0,          # 50 kWh capacity
        ask_price=0.22,
        price_max=0.35,
        emergency_only=True         # Only sells during producer failure
    )
    
    # AGENT REGISTRY
    agents = [
        ("grid_node", grid_node_agent, 10000),
        ("environment", environment_agent, 10001),
    ]
    
    port = 10002
    for i, consumer in enumerate(consumers, start=1):
        agents.append((f"consumer{i}", consumer, port))
        port += 1
    
    for i, prosumer in enumerate(prosumers, start=1):
        agents.append((f"prosumer{i}", prosumer, port))
        port += 1
    
    agents.extend([
        ("solarfarm1", solar_farm, port),
        ("windturbine1", wind_turbine, port + 1),
        ("storage1", storage_mgr, port + 2),
    ])
    
    # STARTUP SEQUENCE
    for name, agent, port_num in agents:
        await agent.start(auto_register=True)
        agent.web.start(hostname="127.0.0.1", port=port_num)
        print(f"✅ {name:15s} started - Web UI: http://127.0.0.1:{port_num}")
    
    setup_time = time.time() - start_time
    print(f"\n✅ All agents started in {setup_time:.2f}s")
    print(f"   {num_consumers} consumers + {num_prosumers} prosumers + 2 producers + 1 storage (50 kWh)")
    print(f"\n🔋 Emergency System Active: Storage reserves energy for producer failures")
    print(f"⚠️  Producer Failure: 20% chance per round (1-4 rounds offline)\n")  # ✅ CORRIGIDO: 5% → 20%
    
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\n\n🛑 Stopping all agents...")
    finally:
        for name, agent, _ in agents:
            try:
                agent.web.stop()
            except Exception:
                pass
            await agent.stop()
        print("✅ Simulation finished.")


if __name__ == "__main__":
    spade.run(main())
