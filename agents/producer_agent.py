import time
import json
import random
import spade
import asyncio
from datetime import datetime
from spade.behaviour import PeriodicBehaviour, CyclicBehaviour, OneShotBehaviour
from spade.message import Message
from logs.db_logger import DBLogger


class ProducerAgent(spade.agent.Agent):
    """
    Producer agent (solar or wind).
    - Periodically sends its generation status to the Grid Node.
    - When a CFP (Call for Proposals) is received, it immediately offers
      its available production at the configured price.
    """

    def __init__(self, jid, password, grid_node_jid, production_type="solar", max_capacity_kw=100.0, ask_price=0.18):
        super().__init__(jid, password)
        self.grid_node_jid = grid_node_jid
        self.production_type = production_type
        self.max_capacity_kw = max_capacity_kw
        self.current_production_kw = 0.0
        self.ask_price = float(ask_price)
        self.active_round_id = None
        self.round_deadline_ts = 0.0

    # Helpers

    def _log_print(self, msg):
        """Print timestamped messages."""
        now = time.strftime("%H:%M:%S")
        print(f"[{now}] {msg}")

    def _add_event(self, kind, kw=0.0, price=0.0, R=None):
        """Store a local event in the database."""
        jid_local = str(self.jid).split('@')[0]
        if hasattr(self, "db_logger"):
            self.db_logger.log_event(kind, jid_local, kw, price, R)

    # Behaviours

    class UpdateProductionBehaviour(PeriodicBehaviour):
        """Periodically updates production and sends status to the Grid Node."""

        async def run(self):
            # Simple generation model by source type
            if self.agent.production_type == "solar":
                current_hour = datetime.now().hour
                if 7 <= current_hour < 19:
                    peak_hour = 13
                    factor = max(0.0, 1 - (abs(current_hour - peak_hour) / 6) ** 2)
                    self.agent.current_production_kw = max(
                        0.0, factor * self.agent.max_capacity_kw + random.uniform(-5, 5)
                    )
                else:
                    self.agent.current_production_kw = 0.0
            else:  # wind
                base = self.agent.max_capacity_kw * random.uniform(0.2, 0.9)
                fluct = self.agent.max_capacity_kw * random.uniform(-0.15, 0.15)
                self.agent.current_production_kw = max(0.0, base + fluct)
                if random.random() < 0.05:  # 5% chance of calm wind
                    self.agent.current_production_kw *= 0.1

            # Log event and send production report
            self.agent._add_event("status", self.agent.current_production_kw, 0.0)

            report = {
                "jid": str(self.agent.jid),
                "prod_kw": self.agent.current_production_kw,
                "cap_kw": self.agent.max_capacity_kw,
                "type": self.agent.production_type,
                "t": time.time(),
            }
            m = Message(to=self.agent.grid_node_jid)
            m.metadata = {"performative": "inform", "type": "production_report"}
            m.body = json.dumps(report)
            await self.send(m)

    class InviteReceiver(CyclicBehaviour):
        """Receives CFP (call for offers) and immediately sends an offer."""

        async def run(self):
            msg = await self.receive(timeout=0.5)
            if not msg or (msg.metadata or {}).get("type") != "call_for_offers":
                return

            try:
                data = json.loads(msg.body)
            except Exception:
                return

            self.agent.active_round_id = data.get("round_id")
            self.agent.round_deadline_ts = float(data.get("deadline_ts", 0.0))
            R = self.agent.active_round_id
            self.agent._add_event("cfp_received", 0.0, 0.0, R)

            now = time.time()
            if now <= self.agent.round_deadline_ts and self.agent.current_production_kw > 0.5:
                offer = {
                    "round_id": R,
                    "offer_kw": self.agent.current_production_kw,
                    "price": self.agent.ask_price,
                    "t": now,
                }
                m = Message(to=self.agent.grid_node_jid)
                m.metadata = {"performative": "propose", "type": "energy_offer"}
                m.body = json.dumps(offer)
                await self.send(m)
                self.agent._add_event("offer_sent", self.agent.current_production_kw, self.agent.ask_price, R)

    class AckReceiver(CyclicBehaviour):
        """Receives acceptance messages for successful offers."""

        async def run(self):
            msg = await self.receive(timeout=0.5)
            if not msg or (msg.metadata or {}).get("type") != "offer_accept":
                return
            try:
                payload = json.loads(msg.body)
            except Exception:
                payload = {}

            kw = float(payload.get("accepted_kw", 0.0))
            price = float(payload.get("price", 0.0))
            R = payload.get("round_id")
            self.agent._add_event("offer_accepted", kw, price, R)

    class StartAfterDelay(OneShotBehaviour):
        """Delays the start of periodic production updates."""

        def __init__(self, delay_s=30):
            super().__init__()
            self.delay_s = delay_s

        async def run(self):
            await asyncio.sleep(self.delay_s)
            self.agent.add_behaviour(self.agent.UpdateProductionBehaviour(period=30))

    async def setup(self):
        """Initializes the producer agent and its behaviours."""
        jid_local = str(self.jid).split('@')[0]
        self._log_print(f"[{jid_local}] Producer Agent ({self.production_type}) started.")
        self.db_logger = DBLogger()
        self.add_behaviour(self.StartAfterDelay(delay_s=30))
        self.add_behaviour(self.InviteReceiver())
        self.add_behaviour(self.AckReceiver())
