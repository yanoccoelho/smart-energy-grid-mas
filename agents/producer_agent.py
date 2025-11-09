# agents/producer_agent.py
import time
import json
import random
import spade
import asyncio
from datetime import datetime
from spade.behaviour import PeriodicBehaviour, CyclicBehaviour, OneShotBehaviour
from spade.message import Message

class ProducerAgent(spade.agent.Agent):
    """
    Produtor (solar/eólico): envia produção ao Grid e, ao receber CFP,
    oferta imediatamente sua disponibilidade ao preço configurado.
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

    class UpdateProductionBehaviour(PeriodicBehaviour):
        async def run(self):
            # Modelo simples de produção por fonte
            if self.agent.production_type == "solar":
                current_hour = datetime.now().hour
                if 7 <= current_hour < 19:
                    peak_hour = 13
                    factor = max(0.0, 1 - (abs(current_hour - peak_hour) / 6) ** 2)
                    self.agent.current_production_kw = max(0.0, factor * self.agent.max_capacity_kw + random.uniform(-5, 5))
                else:
                    self.agent.current_production_kw = 0.0
            else:  # eólica
                base = self.agent.max_capacity_kw * random.uniform(0.2, 0.9)
                fluct = self.agent.max_capacity_kw * random.uniform(-0.15, 0.15)
                self.agent.current_production_kw = max(0.0, base + fluct)
                if random.random() < 0.05:
                    self.agent.current_production_kw *= 0.1

            jid_local = str(self.agent.jid).split('@')[0]
            print(f"[{jid_local} ({self.agent.production_type})] Available={self.agent.current_production_kw:.2f} kW")

            rep = {
                "jid": str(self.agent.jid),
                "prod_kw": self.agent.current_production_kw,
                "cap_kw": self.agent.max_capacity_kw,
                "type": self.agent.production_type,
                "t": time.time(),
            }
            r = Message(to=self.agent.grid_node_jid)
            r.metadata = {"performative": "inform", "type": "production_report"}
            r.body = json.dumps(rep)
            await self.send(r)

    class InviteReceiver(CyclicBehaviour):
        async def run(self):
            msg = await self.receive(timeout=0.5)
            if not msg: return
            if (msg.metadata or {}).get("type") != "call_for_offers": return
            try:
                data = json.loads(msg.body)
            except Exception:
                return
            self.agent.active_round_id = data.get("round_id")
            self.agent.round_deadline_ts = float(data.get("deadline_ts", 0.0))

            now = time.time()
            if now <= self.agent.round_deadline_ts and self.agent.current_production_kw > 0.5:
                off = {
                    "round_id": self.agent.active_round_id,
                    "offer_kw": self.agent.current_production_kw,
                    "price": self.agent.ask_price,
                    "t": now,
                }
                o = Message(to=self.agent.grid_node_jid)
                o.metadata = {"performative": "propose", "type": "energy_offer"}
                o.body = json.dumps(off)
                await self.send(o)

    class AckReceiver(CyclicBehaviour):
        async def run(self):
            msg = await self.receive(timeout=0.5)
            if not msg: return
            if (msg.metadata or {}).get("type") != "offer_accept": return
            try:
                payload = json.loads(msg.body)
            except Exception:
                payload = {}
            jid_local = str(self.agent.jid).split('@')[0]
            print(f"[{jid_local}] Accepted {payload.get('accepted_kw', 0.0):.2f} kW @ {payload.get('price', 0.0):.4f}")

    class StartAfterDelay(OneShotBehaviour):
        def __init__(self, delay_s=30):
            super().__init__()
            self.delay_s = delay_s
        async def run(self):
            await asyncio.sleep(self.delay_s)
            self.agent.add_behaviour(self.agent.UpdateProductionBehaviour(period=30))

    async def setup(self):
        jid_local = str(self.jid).split('@')[0]
        print(f"[{jid_local}] Producer Agent ({self.production_type}) starting...")
        self.add_behaviour(self.StartAfterDelay(delay_s=30))
        self.add_behaviour(self.InviteReceiver())
        self.add_behaviour(self.AckReceiver())
