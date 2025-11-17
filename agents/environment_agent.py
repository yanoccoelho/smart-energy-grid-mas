import time
import json
import random
import spade
from spade.behaviour import CyclicBehaviour
from spade.message import Message
from scenarios.base_config import SCENARIO_CONFIG

class EnvironmentAgent(spade.agent.Agent):
    """Environment Agent - Simulates environmental conditions."""

    def __init__(self, jid, password, broadcast_list, config=SCENARIO_CONFIG):
        super().__init__(jid, password)
        self.broadcast_list = broadcast_list
        self.config = config
        self.temperature_c = self.config["ENVIRONMENT"]["BASE_TEMPERATURE"]
        self.solar_irradiance = 0.8  # 0-1 range
        self.wind_speed = self.config["ENVIRONMENT"]["BASE_WIND_SPEED"]

    async def setup(self):
        """Setup - listen for update requests from GridNode."""
        self.add_behaviour(self.UpdateListener())

    def _calculate_environment(self, sim_hour):
        """Calculate environment data based on SIMULATED hour."""
        # Solar irradiance curve (0-1 range) based on SIMULATED hour
        if 6 <= sim_hour <= 18:
            peak = 12
            self.solar_irradiance = max(
                0.0, 1 - ((sim_hour - peak) / 6) ** 2 + random.uniform(-0.05, 0.05)
            )
        else:
            self.solar_irradiance = 0.0

        # Wind speed (gaussian distribution)
        base_wind = self.config["ENVIRONMENT"]["BASE_WIND_SPEED"]
        wind_noise_min, wind_noise_max = self.config["ENVIRONMENT"]["WIND_NOISE_RANGE"]
        self.wind_speed = max(0.0, base_wind + random.uniform(wind_noise_min, wind_noise_max))

        # Temperature based on time of day (realistic daily cycle)
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
        else:  # 18h-00h
            offset = -0.1
        temp_center = base_temp + offset * temp_variation
        temp_range = temp_variation * 0.2
        self.temperature_c = round(random.uniform(temp_center - temp_range, temp_center + temp_range), 1)

    class UpdateListener(CyclicBehaviour):
        """Listen for update requests from GridNode."""
        
        async def run(self):
            msg = await self.receive(timeout=1.0)
            if not msg:
                return
            
            msg_type = msg.metadata.get("type", "")
            
            if msg_type == "request_environment_update":
                data = json.loads(msg.body)
                sim_hour = data.get("sim_hour", 12)
                
                # Calculate environment based on SIMULATED hour
                self.agent._calculate_environment(sim_hour)
                
                # Broadcast to all agents with simulated hour AND temperature
                broadcast_data = {
                    "solar_irradiance": self.agent.solar_irradiance,
                    "wind_speed": self.agent.wind_speed,
                    "temperature_c": self.agent.temperature_c,
                    "sim_hour": sim_hour
                }
                
                for target_jid in self.agent.broadcast_list:
                    env_msg = Message(to=target_jid)
                    env_msg.metadata = {"performative": "inform", "type": "environment_update"}
                    env_msg.body = json.dumps(broadcast_data)
                    await self.send(env_msg)
