"""
Main simulation launcher for the Smart Energy Grid Multi-Agent System.

This script:
- Loads scenario configurations dynamically
- Allows optional parameter overrides
- Creates and initializes all agents (grid node, environment, households, producers, storage)
- Starts web dashboards for each agent
- Keeps the simulation running until interrupted

All comments were added for clarity; no logic was modified.
"""

import asyncio
import time
import spade
import os
import importlib

# Load base config object
from scenarios.base_config import SCENARIO_CONFIG

# Agent imports
from agents.household_agent import HouseholdAgent
from agents.producer_agent import ProducerAgent
from agents.grid_node_agent import GridNodeAgent
from agents.storage_manager_agent import StorageManagerAgent
from agents.environment_agent import EnvironmentAgent


def load_available_scenarios():
    """
    Scan the 'scenarios' folder for Python files and load their configs.

    Returns:
        dict mapping scenario_key → scenario_title
    """
    scenarios = {}

    folder = "scenarios"

    for filename in os.listdir(folder):
        # Ignore non-Python files
        if not filename.endswith(".py"):
            continue

        # Exclude base config (already processed)
        if filename == "base_config.py":
            continue

        # Convert filename into module name
        scenario_name = filename.replace(".py", "")

        try:
            # Import dynamically
            module = importlib.import_module(f"scenarios.{scenario_name}")
            scenario_title = module.SCENARIO_CONFIG.get("NAME", scenario_name)
            scenarios[scenario_name] = scenario_title
        except Exception as e:
            print(f"⚠️ Warning: Could not load {filename}: {e}")

    # Base config is always added as first entry
    scenarios = {"base_config": "Base Configuration"} | scenarios

    return scenarios


def ask_scenario():
    """
    Display a selection menu to choose the scenario.

    Returns:
        The key of the chosen scenario.
    """
    scenarios = load_available_scenarios()

    print("\nSelect a scenario:\n")
    for idx, (scenario_id, scenario_name) in enumerate(scenarios.items(), start=1):
        print(f"{idx}) {scenario_name}")

    # Loop until user selects a valid scenario number
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


def ask_simulation_overrides(config):
    """
    Allow the user to override the number of consumers & prosumers.

    Args:
        config: SCENARIO_CONFIG dict

    Returns:
        Modified config (in-place).
    """
    print("\nDo you want to customize the simulation parameters?")
    choice = input("Type 'y' to customize, or 'd' to keep defaults: ").strip().lower()

    if choice != "y":
        print("✔ Using default SIMULATION values.\n")
        return config

    # User chooses new values
    try:
        new_consumers = int(input(f"Enter number of consumers (default {config['SIMULATION']['NUM_CONSUMERS']}): "))
        new_prosumers = int(input(f"Enter number of prosumers (default {config['SIMULATION']['NUM_PROSUMERS']}): "))

        config["SIMULATION"]["NUM_CONSUMERS"] = new_consumers
        config["SIMULATION"]["NUM_PROSUMERS"] = new_prosumers

        print("\n✔ Simulation parameters updated.\n")

    except ValueError:
        print("❌ Invalid number. Keeping defaults.\n")

    return config


def load_scenario(scenario_name: str):
    """
    Loads the selected scenario module dynamically and prints its description.

    Args:
        scenario_name: Scenario module name.
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


async def main(config):
    """
    Main simulation coroutine responsible for:
    - Instantiating all agents
    - Starting web dashboards
    - Keeping system alive
    """

    print("=" * 60)
    print("     SMART ENERGY GRID - Multi-Agent System")
    print("=" * 60)

    print("\n🚀 Starting simulation setup...\n")
    start_time = time.time()

    # Extract core simulation parameters from config
    num_consumers = config["SIMULATION"]["NUM_CONSUMERS"]
    num_prosumers = config["SIMULATION"]["NUM_PROSUMERS"]
    xmpp_server = config["SIMULATION"]["XMPP_SERVER"]

    # Environment agent JID template
    env_jid = f"environment@{xmpp_server}"

    # Expected agents count used by the GridNodeAgent for startup sync
    expected_agents = {
        "households": num_consumers + num_prosumers,
        "producers": 2,
        "storage": 1,
    }

    # External grid pricing config (passed to GridNodeAgent)
    external_grid_config = {
        "enabled": True,
        "buy_price_min": config["EXTERNAL_GRID"]["MIN_DYNAMIC_PRICE"],
        "buy_price_max": config["EXTERNAL_GRID"]["SELL_PRICE"],
        "sell_price_min": config["EXTERNAL_GRID"]["BUY_PRICE"],
        "sell_price_max": config["EXTERNAL_GRID"]["MAX_DYNAMIC_PRICE"],
        "acceptance_prob": config["EXTERNAL_GRID"]["ACCEPTANCE_PROB"],
    }

    # GRID NODE AGENT
    grid_node_jid = f"grid_node1@{xmpp_server}"
    grid_node_agent = GridNodeAgent(
        jid=grid_node_jid,
        password="password123",
        expected_agents=expected_agents,
        env_jid=env_jid,
        external_grid_config=external_grid_config,
        config=config,
    )

    # ENVIRONMENT AGENT
    # Composes list of all agents who should receive weather/environment updates
    broadcast_list = (
        [f"consumer{i+1}@{xmpp_server}" for i in range(num_consumers)]
        + [f"prosumer{i+1}@{xmpp_server}" for i in range(num_prosumers)]
        + [f"solarfarm1@{xmpp_server}", f"windturbine1@{xmpp_server}"]
    )

    environment_agent = EnvironmentAgent(
        jid=env_jid,
        password="password123",
        broadcast_list=broadcast_list,
        config=config,
    )

    # HOUSEHOLD AGENTS (Consumers + Prosumers)
    consumers = [
        HouseholdAgent(
            jid=f"consumer{i+1}@{xmpp_server}",
            password="password123",
            grid_node_jid=grid_node_jid,
            is_prosumer=False,
            price_max=0.35,
            config=config,
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
            config=config,
        )
        for i in range(num_prosumers)
    ]

    # PRODUCER AGENTS (Solar + Wind)
    solar_farm = ProducerAgent(
        jid=f"solarfarm1@{xmpp_server}",
        password="password123",
        grid_node_jid=grid_node_jid,
        production_type="solar",
        max_capacity_kw=config["PRODUCERS"]["SOLAR_CAPACITY_KW"],
        ask_price=0.18,
        config=config,
    )

    wind_turbine = ProducerAgent(
        jid=f"windturbine1@{xmpp_server}",
        password="password123",
        grid_node_jid=grid_node_jid,
        production_type="wind",
        max_capacity_kw=config["PRODUCERS"]["WIND_CAPACITY_KW"],
        ask_price=0.19,
        config=config,
    )

    # STORAGE MANAGER AGENT
    storage_mgr = StorageManagerAgent(
        jid=f"storage1@{xmpp_server}",
        password="password123",
        grid_node_jid=grid_node_jid,
        soc_init_frac=1.0,  # fully charged at start
        config=config,
    )

    # List of all agents to start + assign web ports
    agents = [
        ("grid_node", grid_node_agent, 10000),
        ("environment", environment_agent, 10001),
    ]

    port = 10002  # incremental port for each agent’s web dashboard

    # Add consumers
    for i, consumer in enumerate(consumers, start=1):
        agents.append((f"consumer{i}", consumer, port))
        port += 1

    # Add prosumers
    for i, prosumer in enumerate(prosumers, start=1):
        agents.append((f"prosumer{i}", prosumer, port))
        port += 1

    # Add producers + storage
    agents.extend(
        [
            ("solarfarm1", solar_farm, port),
            ("windturbine1", wind_turbine, port + 1),
            ("storage1", storage_mgr, port + 2),
        ]
    )

    # START ALL AGENTS + WEB UI
    for name, agent, port_num in agents:
        await agent.start(auto_register=True)  # start SPADE agent
        agent.web.start(hostname="127.0.0.1", port=port_num)  # start dashboard
        print(f"✅ {name:15} started - Web UI: http://127.0.0.1:{port_num}")

    setup_time = time.time() - start_time
    print(f"\n✔ All agents started in {setup_time:.2f}s\n")

    try:
        # Keep simulation alive
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Stopping all agents...")
    finally:
        # Cleanup
        for name, agent, _ in agents:
            try:
                agent.web.stop()
            except Exception:
                pass

            await agent.stop()

        print("✔ Simulation finished.")


if __name__ == "__main__":
    scenario_name = ask_scenario()
    load_scenario(scenario_name)

    # Optional parameter override
    config = ask_simulation_overrides(SCENARIO_CONFIG)

    # Launch main coroutine using SPADE's event loop
    spade.run(main(SCENARIO_CONFIG))
