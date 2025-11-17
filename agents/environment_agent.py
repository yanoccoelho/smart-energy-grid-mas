import time
import json
import random
import spade
from spade.behaviour import CyclicBehaviour
from spade.message import Message
from scenarios.base_config import SCENARIO_CONFIG


class EnvironmentAgent(spade.agent.Agent):
    """
    EnvironmentAgent simulates environmental conditions such as solar irradiance,
    wind speed, and temperature. It responds to environment update requests sent
    by GridNode agents and broadcasts updated environmental conditions to all
    subscribed agents.

    Args:
        jid (str): XMPP JID of the agent.
        password (str): Password for the XMPP account.
        broadcast_list (list[str]): List of agent JIDs that will receive environment updates.
        config (dict, optional): Configuration dictionary containing environment parameters.
                                 Defaults to SCENARIO_CONFIG.

    Attributes:
        broadcast_list (list[str]): Agents that will receive environment broadcasts.
        config (dict): Scenario configuration with environmental settings.
        temperature_c (float): Current temperature in Celsius.
        solar_irradiance (float): Solar irradiance in the range 0.0–1.0.
        wind_speed (float): Current wind speed.
    """

    def __init__(self, jid, password, broadcast_list, config=SCENARIO_CONFIG):
        super().__init__(jid, password)
        self.broadcast_list = broadcast_list
        self.config = config
        self.temperature_c = self.config["ENVIRONMENT"]["BASE_TEMPERATURE"]
        self.solar_irradiance = 0.8  # 0–1 range
        self.wind_speed = self.config["ENVIRONMENT"]["BASE_WIND_SPEED"]

    async def setup(self):
        """
        Setup the agent by adding the behavior that listens for environment
        update requests from GridNode agents.
        """
        self.add_behaviour(self.UpdateListener())

    def _calculate_environment(self, sim_hour):
        """
        Compute the environmental conditions based on the simulated hour.

        Args:
            sim_hour (int): Simulated hour of the day (0–23).
        """
        # Solar irradiance based on hour (0–1 curve)
        if 6 <= sim_hour <= 18:
            peak = 12
            self.solar_irradiance = max(
                0.0,
                1
                - ((sim_hour - peak) / 6) ** 2
                + random.uniform(-0.05, 0.05)
            )
        else:
            self.solar_irradiance = 0.0

        # Wind speed variation (Gaussian-like noise)
        base_wind = self.config["ENVIRONMENT"]["BASE_WIND_SPEED"]
        wind_noise_min, wind_noise_max = self.config["ENVIRONMENT"]["WIND_NOISE_RANGE"]
        self.wind_speed = max(
            0.0,
            base_wind + random.uniform(wind_noise_min, wind_noise_max)
        )

        # Temperature based on realistic daily cycle
        base_temp = self.config["ENVIRONMENT"]["BASE_TEMPERATURE"]
        temp_variation = self.config["ENVIRONMENT"]["TEMP_VARIATION"]

        if 0 <= sim_hour < 6:
            offset = -0.6
        elif 6 <= sim_hour < 9:
            offset = -0.2
        elif 9 <= sim_hour < 15:
            offset = 0.4
        elif 15 <= sim_hour < 18:
            offset = 0.2
        else:
            offset = -0.1

        temp_center = base_temp + offset * temp_variation
        temp_range = temp_variation * 0.2

        self.temperature_c = round(
            random.uniform(temp_center - temp_range, temp_center + temp_range), 1
        )

    class UpdateListener(CyclicBehaviour):
        """
        Cyclic behaviour that listens for environment update requests sent
        by GridNode agents. When a request is received, the environment is
        recalculated and broadcast to all subscribed agents.
        """

        async def run(self):
            msg = await self.receive(timeout=1.0)
            if not msg:
                return

            msg_type = msg.metadata.get("type", "")

            if msg_type == "request_environment_update":
                data = json.loads(msg.body)
                sim_hour = data.get("sim_hour", 12)

                # Compute new environmental conditions
                self.agent._calculate_environment(sim_hour)

                # Data to broadcast
                broadcast_data = {
                    "solar_irradiance": self.agent.solar_irradiance,
                    "wind_speed": self.agent.wind_speed,
                    "temperature_c": self.agent.temperature_c,
                    "sim_hour": sim_hour,
                }

                # Send to all subscribed agents
                for target_jid in self.agent.broadcast_list:
                    env_msg = Message(to=target_jid)
                    env_msg.metadata = {
                        "performative": "inform",
                        "type": "environment_update",
                    }
                    env_msg.body = json.dumps(broadcast_data)
                    await self.send(env_msg)
