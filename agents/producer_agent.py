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
    - Adjusts its production based on environment data (irradiance, wind speed).
    - Responds to CFPs from the Grid Node with offers.
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

        # Environmental factors
        self.solar_irradiance = 0.0
        self.wind_speed = 0.0

    # Helpers

    def _log_print(self, msg):
        now = time.strftime("%H:%M:%S")
        print(f"[{now}] {msg}")

    def _add_event(self, kind, kw=0.0, price=0.0, R=None):
        jid_local = str(self.jid).split('@')[0]
        if hasattr(self, "db_logger"):
            self.db_logger.log_event(kind, jid_local, kw, price, R)

    # === Behaviours ===

    class EnvironmentReceiver(CyclicBehaviour):
        """Receives environment updates (solar irradiance and wind speed)."""

        async def run(self):
            msg = await self.receive(timeout=0.5)
            if not msg or (msg.metadata or {}).get("type") != "environment_update":
                return
            try:
                data = json.loads(msg.body)
            except Exception:
                return

            # Update environment variables
            self.agent.solar_irradiance = float(data.get("solar_irradiance", 0.0))
            self.agent.wind_speed = float(data.get("wind_speed", 0.0))
            jid_local = str(self.agent.jid).split('@')[0]
            self.agent._log_print(
                f"[{jid_local}] Environment update received: solar={self.agent.solar_irradiance:.2f}, wind={self.agent.wind_speed:.1f} m/s"
            )
            self.agent._add_event("env_update", self.agent.solar_irradiance, 0.0)

    class UpdateProductionBehaviour(PeriodicBehaviour):
        """Periodically updates production based on environment and sends report."""

        async def run(self):
            if self.agent.production_type == "solar":
                # Production proportional to irradiance
                irradiance = getattr(self.agent, "solar_irradiance", 0.0)
                self.agent.current_production_kw = max(
                    0.0, irradiance * self.agent.max_capacity_kw + random.uniform(-3, 3)
                )

            elif self.agent.production_type == "eolic":
                # Production proportional to wind speed (0–15 m/s)
                wind = getattr(self.agent, "wind_speed", 0.0)
                normalized = min(max(wind / 15.0, 0.0), 1.0)
                self.agent.current_production_kw = max(
                    0.0, normalized * self.agent.max_capacity_kw + random.uniform(-5, 5)
                )

            # Log event and send report
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
        def __init__(self, delay_s=30):
            super().__init__()
            self.delay_s = delay_s

        async def run(self):
            await asyncio.sleep(self.delay_s)
            self.agent.add_behaviour(self.agent.UpdateProductionBehaviour(period=30))

    async def setup(self):
        jid_local = str(self.jid).split('@')[0]
        self._log_print(f"[{jid_local}] Producer Agent ({self.production_type}) started.")
        self.db_logger = DBLogger()
        self.add_behaviour(self.StartAfterDelay(delay_s=30))
        self.add_behaviour(self.InviteReceiver())
        self.add_behaviour(self.AckReceiver())
        self.add_behaviour(self.EnvironmentReceiver())
