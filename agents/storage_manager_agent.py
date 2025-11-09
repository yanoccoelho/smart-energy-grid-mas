import time
import json
import random
import spade
import asyncio
from spade.behaviour import PeriodicBehaviour, OneShotBehaviour, CyclicBehaviour
from spade.message import Message
from logs.db_logger import DBLogger


class StorageManagerAgent(spade.agent.Agent):
    """
    Energy Storage Manager agent.
    - Periodically reports battery telemetry (SoC, SoH, temperature).
    - Sends proactive purchase requests if SoC is low during active rounds.
    - Sends offers when invited and SoC > 20% of total capacity.
    """

    def __init__(
        self,
        jid,
        password,
        grid_node_jid,
        soc_init_frac=0.5,
        capacity_kwh=10.0,
        ask_price=0.22,
        price_max=0.28,
    ):
        super().__init__(jid, password)
        self.grid_node_jid = grid_node_jid
        self.cap_kwh = float(capacity_kwh)
        self.soc_kwh = float(soc_init_frac) * float(capacity_kwh)
        self.temp_c = 25.0
        self.soh = 1.0
        self.ask_price = float(ask_price)
        self.price_max = float(price_max)
        self.active_round_id = None
        self.round_deadline_ts = 0.0

    # Helpers

    def _log_print(self, msg):
        """Print timestamped messages."""
        now = time.strftime("%H:%M:%S")
        print(f"[{now}] {msg}")

    def _add_event(self, kind, kw=0.0, price=0.0, R=None):
        """Store local events in the database."""
        jid_local = str(self.jid).split('@')[0]
        if hasattr(self, "db_logger"):
            self.db_logger.log_event(kind, jid_local, kw, price, R)

    # Behaviours

    class Monitor(PeriodicBehaviour):
        """Sends periodic telemetry and triggers proactive purchases if SoC is low."""

        async def run(self):
            # Update telemetry
            self.agent.temp_c += random.uniform(-0.2, 0.2)
            self.agent.soh = max(0.7, self.agent.soh - random.uniform(0.0, 0.0001))

            soc = self.agent.soc_kwh
            cap = self.agent.cap_kwh
            self.agent._add_event("status", soc, 0.0)

            # Send telemetry report to the Grid Node
            report = {
                "jid": str(self.agent.jid),
                "soc_kwh": soc,
                "cap_kwh": cap,
                "temp_c": self.agent.temp_c,
                "soh": self.agent.soh,
                "t": time.time(),
            }
            m = Message(to=self.agent.grid_node_jid)
            m.metadata = {"performative": "inform", "type": "statusBattery"}
            m.body = json.dumps(report)
            await self.send(m)

            # Proactive purchase if SoC < 10% during an active round
            R = getattr(self.agent, "active_round_id", None)
            now = time.time()
            if soc < 0.1 * cap and R and now <= self.agent.round_deadline_ts:
                need = max(0.0, 0.25 * cap)
                req = {
                    "round_id": R,
                    "need_kw": need,
                    "price_max": self.agent.price_max,
                    "t": now,
                }
                r = Message(to=self.agent.grid_node_jid)
                r.metadata = {"performative": "request", "type": "energy_request"}
                r.body = json.dumps(req)
                await self.send(r)
                self.agent._add_event("proactive_request", need, self.agent.price_max, R)

    class InviteReceiver(CyclicBehaviour):
        """Receives CFP (call for offers) and sends an offer if SoC is sufficient."""

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
            if now <= self.agent.round_deadline_ts and self.agent.soc_kwh > 0.2 * self.agent.cap_kwh:
                sell_kw = max(0.0, 0.10 * self.agent.cap_kwh)
                offer = {
                    "round_id": R,
                    "offer_kw": sell_kw,
                    "price": self.agent.ask_price,
                    "t": now,
                }
                o = Message(to=self.agent.grid_node_jid)
                o.metadata = {"performative": "propose", "type": "energy_offer"}
                o.body = json.dumps(offer)
                await self.send(o)
                self.agent._add_event("offer_sent", sell_kw, self.agent.ask_price, R)

    class StartAfterDelay(OneShotBehaviour):
        """Delays the start of telemetry transmission."""

        def __init__(self, delay_s=30):
            super().__init__()
            self.delay_s = delay_s

        async def run(self):
            await asyncio.sleep(self.delay_s)
            self.agent.add_behaviour(self.agent.Monitor(period=30))

    async def setup(self):
        """Initializes the storage manager agent and starts behaviours."""
        jid_local = str(self.jid).split('@')[0]
        self._log_print(f"[{jid_local}] Storage Manager Agent started.")
        self.db_logger = DBLogger()
        self.add_behaviour(self.StartAfterDelay(delay_s=30))
        self.add_behaviour(self.InviteReceiver())
