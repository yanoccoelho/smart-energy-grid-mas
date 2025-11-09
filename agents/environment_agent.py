import time
import json
import random
import spade
from spade.behaviour import PeriodicBehaviour
from spade.message import Message


class EnvironmentAgent(spade.agent.Agent):
    """
    Environment Agent
    -----------------
    Simulates external environmental conditions that affect
    the entire smart grid, such as sunlight, wind, and temperature.
    """

    def __init__(self, jid, password, broadcast_list):
        super().__init__(jid, password)
        self.broadcast_list = broadcast_list  # list of JIDs (producers, prosumers, storage)
        self.temperature_c = 25.0
        self.solar_irradiance = 0.8
        self.wind_speed = 5.0

    class UpdateEnvironment(PeriodicBehaviour):
        async def run(self):
            # Simulate natural variations
            hour = time.localtime().tm_hour

            # Solar irradiance curve (day/night)
            if 6 <= hour <= 18:
                peak = 12
                self.agent.solar_irradiance = max(
                    0.0, 1 - ((hour - peak) / 6) ** 2 + random.uniform(-0.05, 0.05)
                )
            else:
                self.agent.solar_irradiance = 0.0

            # Wind and temperature
            self.agent.wind_speed = max(0.0, random.gauss(6, 2))
            self.agent.temperature_c += random.uniform(-0.3, 0.3)

            env_data = {
                "t": time.time(),
                "solar_irradiance": round(self.agent.solar_irradiance, 3),
                "wind_speed": round(self.agent.wind_speed, 2),
                "temperature_c": round(self.agent.temperature_c, 2),
            }

            for target in self.agent.broadcast_list:
                msg = Message(to=target)
                msg.metadata = {"performative": "inform", "type": "environment_update"}
                msg.body = json.dumps(env_data)
                await self.send(msg)

            print(f"[Environment] Sent update: {env_data}")

    async def setup(self):
        print(f"[{str(self.jid).split('@')[0]}] Environment Agent starting...")
        self.add_behaviour(self.UpdateEnvironment(period=30))
