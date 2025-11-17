import asyncio
import time
import spade
import os
import importlib

# Load base config object
from scenarios.base_config import SCENARIO_CONFIG

# Agents
from agents.household_agent import HouseholdAgent
from agents.producer_agent import ProducerAgent
from agents.grid_node_agent import GridNodeAgent
from agents.storage_manager_agent import StorageManagerAgent
from agents.environment_agent import EnvironmentAgent


def load_available_scenarios():
    """
    Scans the 'scenarios' folder and dynamically loads scenario files.
    Reads SCENARIO_CONFIG['NAME'] from each one.
    """
    scenarios = {}

    folder = "scenarios"

    for filename in os.listdir(folder):
        if not filename.endswith(".py"):
            continue

        # Ignore base config
        if filename == "base_config.py":
            continue

        scenario_name = filename.replace(".py", "")

        try:
            module = importlib.import_module(f"scenarios.{scenario_name}")
            scenario_title = module.SCENARIO_CONFIG.get("NAME", scenario_name)
            scenarios[scenario_name] = scenario_title
        except Exception as e:
            print(f"⚠️ Warning: Could not load {filename}: {e}")

    # Base config always included
    scenarios = {
        "base_config": "Base Configuration"
    } | scenarios

    return scenarios


def ask_scenario():
    """
    Displays a menu where the user chooses which scenario to run.
    """
    scenarios = load_available_scenarios()

    print("\nSelect a scenario:\n")
    for idx, (scenario_id, scenario_name) in enumerate(scenarios.items(), start=1):
        print(f"{idx}) {scenario_name}")

    while True:
        try:
            choice = int(input("\nEnter the scenario number: "))
            if 1 <= choice <= len(scenarios):
                break
            print("Invalid number. Try again.")
        except ValueError:
            print("Invalid input. Please enter a number.")

    scenario_key = list(scenarios.keys())[choice - 1]
    return scenario_key


def load_scenario(scenario_name: str):
    """
    Imports scenario file dynamically and applies overrides to SCENARIO_CONFIG.
    """
    if scenario_name == "base_config":
        print("\n✔️ Using base configuration.\n")
        return

    try:
        module = importlib.import_module(f"scenarios.{scenario_name}")
        print(f"\nScenario selected: {module.SCENARIO_CONFIG['NAME']}")
        print(f"{module.SCENARIO_CONFIG['DESCRIPTION']}\n")
    except Exception as e:
        print(f"❌ Error loading scenario: {e}")
        exit(1)


# MAIN SIMULATION

async def main(config):
    scenario_name = ask_scenario()
    load_scenario(scenario_name)


    print("=" * 60)
    print("     SMART ENERGY GRID - Multi-Agent System")
    print("=" * 60)

    print("\n🚀 Starting simulation setup...\n")
    start_time = time.time()

    # Extract config values
    num_consumers = config["SIMULATION"]["NUM_CONSUMERS"]
    num_prosumers = config["SIMULATION"]["NUM_PROSUMERS"]
    xmpp_server = config["SIMULATION"]["XMPP_SERVER"]

    # Environment JID
    env_jid = f"environment@{xmpp_server}"

    expected_agents = {
        "households": num_consumers + num_prosumers,
        "producers": 2,
        "storage": 1
    }

    # External grid
    external_grid_config = {
        "enabled": True,
        "buy_price_min": config["EXTERNAL_GRID"]["MIN_DYNAMIC_PRICE"],
        "buy_price_max": config["EXTERNAL_GRID"]["SELL_PRICE"],
        "sell_price_min": config["EXTERNAL_GRID"]["BUY_PRICE"],
        "sell_price_max": config["EXTERNAL_GRID"]["MAX_DYNAMIC_PRICE"],
        "acceptance_prob": config["EXTERNAL_GRID"]["ACCEPTANCE_PROB"],
    }

    # GRID NODE
    grid_node_jid = f"grid_node1@{xmpp_server}"
    grid_node_agent = GridNodeAgent(
        jid=grid_node_jid,
        password="password123",
        expected_agents=expected_agents,
        env_jid=env_jid,
        external_grid_config=external_grid_config,
        config=config
    )

    # ENVIRONMENT
    broadcast_list = (
        [f"consumer{i+1}@{xmpp_server}" for i in range(num_consumers)] +
        [f"prosumer{i+1}@{xmpp_server}" for i in range(num_prosumers)] +
        [f"solarfarm1@{xmpp_server}", f"windturbine1@{xmpp_server}"]
    )

    environment_agent = EnvironmentAgent(
        jid=env_jid,
        password="password123",
        broadcast_list=broadcast_list,
        config=config
    )

    # HOUSEHOLDS — consumers + prosumers
    consumers = [
        HouseholdAgent(
            jid=f"consumer{i+1}@{xmpp_server}",
            password="password123",
            grid_node_jid=grid_node_jid,
            is_prosumer=False,
            price_max=0.35,
            config=config
        )
        for i in range(num_consumers)
    ]

    prosumers = [
        HouseholdAgent(
            jid=f"prosumer{i+1}@{xmpp_server}",
            password="password123",
            grid_node_jid=grid_node_jid,
            is_prosumer=True,
            price_max=0.35,
            ask_price=0.20,
            config=config
        )
        for i in range(num_prosumers)
    ]

    # PRODUCERS
    solar_farm = ProducerAgent(
        jid=f"solarfarm1@{xmpp_server}",
        password="password123",
        grid_node_jid=grid_node_jid,
        production_type="solar",
        max_capacity_kw=config["PRODUCERS"]["SOLAR_CAPACITY_KW"],
        ask_price=0.18,
        config=config
    )

    wind_turbine = ProducerAgent(
        jid=f"windturbine1@{xmpp_server}",
        password="password123",
        grid_node_jid=grid_node_jid,
        production_type="wind",
        max_capacity_kw=config["PRODUCERS"]["WIND_CAPACITY_KW"],
        ask_price=0.19,
        config=config
    )

    # STORAGE
    storage_mgr = StorageManagerAgent(
        jid=f"storage1@{xmpp_server}",
        password="password123",
        grid_node_jid=grid_node_jid,
        soc_init_frac=1.0,
        config=config
    )

    # REGISTER AGENTS
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

    # STARTUP
    for name, agent, port_num in agents:
        await agent.start(auto_register=True)
        agent.web.start(hostname="127.0.0.1", port=port_num)
        print(f"✅ {name:15} started - Web UI: http://127.0.0.1:{port_num}")

    setup_time = time.time() - start_time
    print(f"\n✅ All agents started in {setup_time:.2f}s\n")

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Stopping all agents...")
    finally:
        for name, agent, _ in agents:
            try:
                agent.web.stop()
            except Exception:
                pass

            await agent.stop()

        print("✅ Simulation finished.")


if __name__ == "__main__":
    spade.run(main(SCENARIO_CONFIG))
