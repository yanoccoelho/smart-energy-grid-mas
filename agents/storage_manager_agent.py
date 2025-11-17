import time
import json
import random
import spade
import asyncio
from spade.behaviour import PeriodicBehaviour, OneShotBehaviour, CyclicBehaviour
from spade.message import Message
from logs.db_logger import DBLogger
from scenarios.base_config import SCENARIO_CONFIG

class StorageManagerAgent(spade.agent.Agent):
    """Energy Storage Manager agent - Emergency Reserve (50 kWh)."""

    def __init__(
        self,
        jid,
        password,
        grid_node_jid,
        soc_init_frac=1.0,
        config=SCENARIO_CONFIG,
    ):
        super().__init__(jid, password)
        self.grid_node_jid = grid_node_jid
        self.cap_kwh = float(config["STORAGE"]["CAPACITY_KWH"])
        self.soc_kwh = float(soc_init_frac) * self.cap_kwh
        self.temp_c = 25.0
        self.soh = 1.0
        self.ask_price = float(config["STORAGE"]["ASK_PRICE"])
        self.price_max = float(config["STORAGE"]["MAX_PRICE"])
        self.emergency_only = config["STORAGE"]["EMERGENCY_ONLY"]
        self.response_probability = 1.0
        self.active_round_id = None
        self.round_deadline_ts = 0.0

    async def setup(self):
        """Initialize agent and send registration."""
        self.add_behaviour(self.InitialSetup())
        self.add_behaviour(self.StartAfterDelay())
        self.add_behaviour(self.RoundReceiver())

    class InitialSetup(OneShotBehaviour):
        """Send registration and initial status immediately."""
        async def run(self):
            register_msg = Message(to=self.agent.grid_node_jid)
            register_msg.metadata = {"performative": "inform", "type": "register_storage"}
            register_msg.body = json.dumps({
                "jid": str(self.agent.jid),
                "capacity_kwh": self.agent.cap_kwh,
                "emergency_only": self.agent.emergency_only,
                "timestamp": time.time()
            })
            await self.send(register_msg)
            
            status_msg = Message(to=self.agent.grid_node_jid)
            status_msg.metadata = {"performative": "inform", "type": "statusBattery"}
            status_msg.body = json.dumps({
                "jid": str(self.agent.jid),
                "soc_kwh": self.agent.soc_kwh,
                "cap_kwh": self.agent.cap_kwh,
                "temp_c": self.agent.temp_c,
                "soh": self.agent.soh,
                "emergency_only": self.agent.emergency_only,
                "timestamp": time.time()
            })
            await self.send(status_msg)

    class StartAfterDelay(OneShotBehaviour):
        """Wait 30 seconds, then start periodic updates."""
        def __init__(self):
            super().__init__()
            self.delay_seconds = 30.0

        async def run(self):
            await asyncio.sleep(self.delay_seconds)
            self.agent.add_behaviour(self.agent.Monitor(period=30.0))

    class Monitor(PeriodicBehaviour):
        """Periodically send battery status."""
        async def run(self):
            msg = Message(to=self.agent.grid_node_jid)
            msg.metadata = {"performative": "inform", "type": "statusBattery"}
            msg.body = json.dumps({
                "jid": str(self.agent.jid),
                "soc_kwh": self.agent.soc_kwh,
                "cap_kwh": self.agent.cap_kwh,
                "temp_c": self.agent.temp_c,
                "soh": self.agent.soh,
                "emergency_only": self.agent.emergency_only,
                "timestamp": time.time()
            })
            await self.send(msg)

    class RoundReceiver(CyclicBehaviour):
        """Listen for CFPs and respond based on emergency status."""
        async def run(self):
            msg = await self.receive(timeout=0.5)
            if not msg:
                return

            msg_type = msg.metadata.get("type", "")
            
            if msg_type == "call_for_offers":
                data = json.loads(msg.body)
                self.agent.active_round_id = data.get("round_id")
                self.agent.round_deadline_ts = data.get("deadline_ts", 0)
                producers_failed = data.get("producers_failed", False)
                
                soc_pct = self.agent.soc_kwh / self.agent.cap_kwh
                
                # ✅ EMERGENCY MODE LOGIC
                if self.agent.emergency_only:
                    if producers_failed and soc_pct > 0.20:
                        # Emergency: Offer energy
                        self.agent.add_behaviour(self.agent.OfferEnergyEmergency())
                    elif soc_pct < 0.99:
                        # Recharge: Request energy (normal priority)
                        self.agent.add_behaviour(self.agent.RequestEnergy())
                else:
                    # Normal mode (old behavior)
                    if soc_pct < 0.95:
                        self.agent.add_behaviour(self.agent.RequestEnergy())
                    elif soc_pct >= 0.95:
                        self.agent.add_behaviour(self.agent.OfferEnergy())
            
            elif msg_type in ["control_command", "offer_accept"]:
                data = json.loads(msg.body)
                kwh = data.get("kw", 0)
                
                if msg_type == "control_command":
                    # Bought energy - increase SOC
                    self.agent.soc_kwh = min(self.agent.soc_kwh + kwh, self.agent.cap_kwh)
                elif msg_type == "offer_accept":
                    # Sold energy - decrease SOC
                    self.agent.soc_kwh = max(self.agent.soc_kwh - kwh, 0)

    class RequestEnergy(OneShotBehaviour):
        """Request energy to recharge (normal priority)."""
        async def run(self):
            R = self.agent.active_round_id
            if not R:
                return
            
            needed = self.agent.cap_kwh - self.agent.soc_kwh
            
            if needed > 0.5:
                base_price_max = self.agent.price_max
                price_variation = random.uniform(-0.02, 0.02)
                final_price_max = base_price_max * (1 + price_variation)
                
                msg = Message(to=self.agent.grid_node_jid)
                msg.metadata = {"performative": "request", "type": "energy_request"}
                msg.body = json.dumps({
                    "round_id": R,
                    "need_kwh": needed,
                    "price_max": round(final_price_max, 2)
                })
                await self.send(msg)

    class OfferEnergyEmergency(OneShotBehaviour):
        """Offer ALL available energy during emergencies (no limits)."""
        async def run(self):
            R = self.agent.active_round_id
            if not R:
                return
            
            # Keep 20% minimum reserve
            min_reserve = 0.20 * self.agent.cap_kwh
            available = self.agent.soc_kwh - min_reserve
            
            if available > 0.5:
                now = time.time()
                if now <= self.agent.round_deadline_ts:
                    final_price = self.agent.ask_price
                    
                    msg = Message(to=self.agent.grid_node_jid)
                    msg.metadata = {"performative": "propose", "type": "energy_offer"}
                    msg.body = json.dumps({
                        "round_id": R,
                        "offer_kwh": available,
                        "price": final_price,
                        "emergency": True
                    })
                    await self.send(msg)

    class OfferEnergy(OneShotBehaviour):
        """Send energy offer when SOC is sufficient (normal mode only)."""
        async def run(self):
            R = self.agent.active_round_id
            if not R:
                return
            
            if random.random() > self.agent.response_probability:
                decline_msg = Message(to=self.agent.grid_node_jid)
                decline_msg.metadata = {"performative": "refuse", "type": "declined_offer"}
                decline_msg.body = json.dumps({"round_id": R, "reason": "agent_decision"})
                await self.send(decline_msg)
                return
            
            # Keep 20% reserve
            available = self.agent.soc_kwh - 0.20 * self.agent.cap_kwh
            
            if available > 0:
                now = time.time()
                if now <= self.agent.round_deadline_ts:
                    base_price = self.agent.ask_price
                    price_variation = random.uniform(-0.01, 0.01)
                    final_price = base_price * (1 + price_variation)
                    
                    msg = Message(to=self.agent.grid_node_jid)
                    msg.metadata = {"performative": "propose", "type": "energy_offer"}
                    msg.body = json.dumps({
                        "round_id": R,
                        "offer_kwh": available,
                        "price": round(final_price, 2)
                    })
                    await self.send(msg)
