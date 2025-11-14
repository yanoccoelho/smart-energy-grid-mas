import time
import json
import random
import spade
from spade.behaviour import CyclicBehaviour, OneShotBehaviour
from spade.message import Message
from logs.db_logger import DBLogger

class ProducerAgent(spade.agent.Agent):
    """Producer agent (solar or wind) with failure simulation."""

    def __init__(self, jid, password, grid_node_jid, production_type="solar", max_capacity_kw=100.0, ask_price=0.18, response_probability=0.85):
        super().__init__(jid, password)
        self.grid_node_jid = grid_node_jid
        self.production_type = production_type
        self.max_capacity_kwh = max_capacity_kw
        self.current_production_kwh = 0.0
        self.ask_price = float(ask_price)
        self.response_probability = float(response_probability)
        
        self.is_operational = True
        self.failure_rounds_remaining = 0
        self.failure_rounds_total = 0
        self.last_decrement_round = None
        self.active_round_id = None
        self.round_deadline_ts = 0.0
        self.solar_irradiance = 0.0
        self.wind_speed = 0.0
        self.temperature = 20.0
        self.db_logger = DBLogger()

    async def setup(self):
        self.add_behaviour(self.InitialSetup())
        self.add_behaviour(self.RoundReceiver())

    def _update_production(self):
        """Calculate production with failure simulation."""
        if not self.is_operational:
            self.current_production_kwh = 0.0
            return
        
        if self.production_type == "solar":
            if self.solar_irradiance > 0:
                efficiency = 0.20
                prod_kwh = self.solar_irradiance * efficiency * self.max_capacity_kwh
                self.current_production_kwh = min(prod_kwh, self.max_capacity_kwh)
            else:
                self.current_production_kwh = 0.0
        elif self.production_type == "wind":
            if self.wind_speed > 3.0:
                if self.wind_speed < 12.0:
                    power_fraction = (self.wind_speed - 3.0) / 9.0
                else:
                    power_fraction = 1.0
                self.current_production_kwh = power_fraction * self.max_capacity_kwh
            else:
                self.current_production_kwh = 0.0

    class InitialSetup(OneShotBehaviour):
        async def run(self):
            register_msg = Message(to=self.agent.grid_node_jid)
            register_msg.metadata = {"performative": "inform", "type": "register_producer"}
            register_msg.body = json.dumps({
                "jid": str(self.agent.jid),
                "production_type": self.agent.production_type,
                "max_capacity_kwh": self.agent.max_capacity_kwh,
                "timestamp": time.time()
            })
            await self.send(register_msg)
            
            self.agent._update_production()
            
            prod_msg = Message(to=self.agent.grid_node_jid)
            prod_msg.metadata = {"performative": "inform", "type": "production_report"}
            prod_msg.body = json.dumps({
                "jid": str(self.agent.jid),
                "prod_kwh": self.agent.current_production_kwh,
                "type": self.agent.production_type,
                "is_operational": self.agent.is_operational,
                "failure_rounds_remaining": self.agent.failure_rounds_remaining,
                "failure_rounds_total": self.agent.failure_rounds_total,
                "solar_irradiance": self.agent.solar_irradiance,
                "wind_speed": self.agent.wind_speed,
                "temperature_c": self.agent.temperature,
                "timestamp": time.time()
            })
            await self.send(prod_msg)

    class RoundReceiver(CyclicBehaviour):
        async def run(self):
            msg = await self.receive(timeout=0.5)
            if not msg:
                return

            msg_type = msg.metadata.get("type", "")
            
            if msg_type == "environment_update":
                data = json.loads(msg.body)
                self.agent.solar_irradiance = data.get("solar_irradiance", 0)
                self.agent.wind_speed = data.get("wind_speed", 0)
                self.agent.temperature = data.get("temperature_c", 20)
                
                if not self.agent.is_operational:
                    current_round = self.agent.active_round_id
                    if current_round and current_round != self.agent.last_decrement_round:
                        if self.agent.failure_rounds_remaining > 0:
                            self.agent.failure_rounds_remaining -= 1
                            self.agent.last_decrement_round = current_round
                            
                            if self.agent.failure_rounds_remaining == 0:
                                self.agent.is_operational = True
                                print(f"🔧 {self.agent.jid} auto-recovered!")
                
                self.agent._update_production()
                
                prod_msg = Message(to=self.agent.grid_node_jid)
                prod_msg.metadata = {"performative": "inform", "type": "production_report"}
                prod_msg.body = json.dumps({
                    "jid": str(self.agent.jid),
                    "prod_kwh": self.agent.current_production_kwh,
                    "type": self.agent.production_type,
                    "is_operational": self.agent.is_operational,
                    "failure_rounds_remaining": self.agent.failure_rounds_remaining,
                    "failure_rounds_total": self.agent.failure_rounds_total,
                    "solar_irradiance": self.agent.solar_irradiance,
                    "wind_speed": self.agent.wind_speed,
                    "temperature_c": self.agent.temperature,
                    "timestamp": time.time()
                })
                await self.send(prod_msg)
                
            elif msg_type == "call_for_offers":
                data = json.loads(msg.body)
                self.agent.active_round_id = data.get("round_id")
                self.agent.round_deadline_ts = data.get("deadline_ts", 0)
                self.agent.add_behaviour(self.agent.OfferBehaviour())
                
            elif msg_type == "offer_accept":
                pass

    class OfferBehaviour(OneShotBehaviour):
        async def run(self):
            R = self.agent.active_round_id
            
            if (not R or
                not self.agent.is_operational or
                self.agent.current_production_kwh <= 0.01):
                return
            
            if random.random() > self.agent.response_probability:
                decline_msg = Message(to=self.agent.grid_node_jid)
                decline_msg.metadata = {"performative": "refuse", "type": "declined_offer"}
                decline_msg.body = json.dumps({"round_id": R, "reason": "agent_decision"})
                await self.send(decline_msg)
                return
            
            now = time.time()
            if now <= self.agent.round_deadline_ts:
                base_price = self.agent.ask_price
                price_variation = random.uniform(-0.02, 0.02)
                final_price = base_price * (1 + price_variation)
                
                msg = Message(to=self.agent.grid_node_jid)
                msg.metadata = {"performative": "propose", "type": "energy_offer"}
                msg.body = json.dumps({
                    "round_id": R,
                    "offer_kwh": self.agent.current_production_kwh,
                    "price": round(final_price, 2)
                })
                await self.send(msg)
