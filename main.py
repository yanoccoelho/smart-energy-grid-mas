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
from copy import deepcopy

# Load base config object
from scenarios.base_config import clone_config

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
        return clone_config()

    try:
        module = importlib.import_module(f"scenarios.{scenario_name}")
        scenario_config = deepcopy(module.SCENARIO_CONFIG)
        print(f"\nScenario selected: {scenario_config['NAME']}")
        print(f"{scenario_config['DESCRIPTION']}\n")
        return scenario_config
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
    xmpp_server = config["SIMULATION"]["XMPP_SERVER"]
    port = 10000

    # Environment agent JID template
    env_jid = f"environment@{xmpp_server}"
   
    agents = []
    global_broadcast_list = []

    # Expected agents count used by the GridNodeAgent for startup sync
    for neighborhood in config["NEIGHBORHOODS"]:
        neighborhood_info = config["NEIGHBORHOODS"][neighborhood]

        num_consumers = neighborhood_info["NUM_CONSUMERS"]
        num_prosumers = neighborhood_info["NUM_PROSUMERS"]
        num_solar_farm = neighborhood_info["PRODUCERS"]["SOLAR_FARMS"]
        num_wind_turbine = neighborhood_info["PRODUCERS"]["WIND_TURBINES"]
        num_storages = neighborhood_info["NUM_STORAGES"]

        expected_agents = {
            "households": num_consumers + num_prosumers,
            "producers": num_solar_farm + num_wind_turbine,
            "storage": num_storages,
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
        grid_node_jid = f"grid_node_{neighborhood}@{xmpp_server}"
        grid_node_agent = GridNodeAgent(
            jid=grid_node_jid,
            password="password123",
            expected_agents=expected_agents,
            neighborhood=neighborhood,
            env_jid=env_jid,
            external_grid_config=external_grid_config,
            config=config,
        )

        # Composes list of all agents who should receive weather/environment updates
        global_broadcast_list  += (
            [f"prosumer_{neighborhood}_{i+1}@{xmpp_server}" for i in range(num_prosumers)]
            + [f"solarfarm_{neighborhood}_{i+1}@{xmpp_server}" for i in range(num_solar_farm)]
            + [f"windturbine_{neighborhood}_{i+1}@{xmpp_server}" for i in range(num_wind_turbine)]
        )

        # HOUSEHOLD AGENTS (Consumers + Prosumers)
        consumers = [
            HouseholdAgent(
                jid=f"consumer_{neighborhood}_{i+1}@{xmpp_server}",
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
                jid=f"prosumer_{neighborhood}_{i+1}@{xmpp_server}",
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
        solar_farms = [
            ProducerAgent(
                jid=f"solarfarm_{neighborhood}_{i+1}@{xmpp_server}",
                password="password123",
                grid_node_jid=grid_node_jid,
                production_type="solar",
                max_capacity_kw=config["PRODUCERS"]["SOLAR_CAPACITY_KW"],
                ask_price=0.18,
                config=config,
            )
            for i in range(num_solar_farm)
        ]


        wind_turbines = [
            ProducerAgent(
                jid=f"windturbine_{neighborhood}_{i+1}@{xmpp_server}",
                password="password123",
                grid_node_jid=grid_node_jid,
                production_type="wind",
                max_capacity_kw=config["PRODUCERS"]["WIND_CAPACITY_KW"],
                ask_price=0.19,
                config=config,
            )
            for i in range(num_wind_turbine)
        ]

        # STORAGE MANAGER AGENT
        storage_mgrs = [
            StorageManagerAgent(
                jid=f"storage_{neighborhood}_{i+1}@{xmpp_server}",
                password="password123",
                grid_node_jid=grid_node_jid,
                soc_init_frac=1.0,  # fully charged at start
                config=config,
            )
            for i in range(num_storages)
        ]

        # List of all agents to start + assign web ports
        agents.append(("grid_node", grid_node_agent, port))
        port += 1

        # Add consumers
        for i, consumer in enumerate(consumers, start=1):
            agents.append((f"consumer_{neighborhood}_{i}", consumer, port))
            port += 1

        # Add prosumers
        for i, prosumer in enumerate(prosumers, start=1):
            agents.append((f"prosumer_{neighborhood}_{i}", prosumer, port))
            port += 1

        # Add solar producers
        for i, solar_farm in enumerate(solar_farms, start=1):
            agents.append((f"solarfarm_{neighborhood}_{i}", solar_farm, port))
            port += 1

        # Add wind producers
        for i, wind_turbine in enumerate(wind_turbines, start=1):
            agents.append((f"windturbine_{neighborhood}_{i}", wind_turbine, port))
            port += 1

        # Add storage
        for i, storage_mgr in enumerate(storage_mgrs, start=1):
            agents.append((f"storage_{neighborhood}_{i}", storage_mgr, port))
            port += 1

    # Environment Agent
    environment_agent = EnvironmentAgent(
        jid=env_jid,
        password="password123",
        broadcast_list=global_broadcast_list,
        config=config,
    )
    

    # --- Start Environment FIRST ---
    await environment_agent.start(auto_register=True)
    environment_agent.web.start(hostname="127.0.0.1", port=port)
    print(f"[Init] Environment started at port {port}")
    port += 1

    # Wait so behaviours finish loading
    await asyncio.sleep(2)

    # 2) start all remaining agents
    for name, agent, port_num in agents:
        await agent.start(auto_register=True)
        agent.web.start(hostname="127.0.0.1", port=port_num)

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
    scenario_config = load_scenario(scenario_name)

    # Optional parameter override
    config = ask_simulation_overrides(scenario_config)

    # Launch main coroutine using SPADE's event loop
    spade.run(main(config))
