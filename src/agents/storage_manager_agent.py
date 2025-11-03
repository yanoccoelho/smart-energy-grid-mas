# agents/storage_manager_agent.py
import time
import json
import random
import spade
import asyncio
from spade.behaviour import PeriodicBehaviour, OneShotBehaviour, CyclicBehaviour
from spade.message import Message

class StorageManagerAgent(spade.agent.Agent):
    """
    Storage: telemetria periódica (SoC/SoH/Temp), compra proativa se SoC baixo
    durante o leilão e oferta sob convite quando SoC > 20% da capacidade.
    """

    def __init__(self, jid, password, grid_node_jid, soc_init_frac=0.5, capacity_kwh=10.0, ask_price=0.22, price_max=0.28):
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

    class Monitor(PeriodicBehaviour):
        async def run(self):
            # Telemetria simples
            self.agent.temp_c += random.uniform(-0.2, 0.2)
            self.agent.soh = max(0.7, self.agent.soh - random.uniform(0.0, 0.0001))
            status = {
                "jid": str(self.agent.jid),
                "soc_kwh": self.agent.soc_kwh,
                "cap_kwh": self.agent.cap_kwh,
                "temp_c": self.agent.temp_c,
                "soh": self.agent.soh,
                "t": time.time(),
            }
            m = Message(to=self.agent.grid_node_jid)
            m.metadata = {"performative": "inform", "type": "statusBattery"}
            m.body = json.dumps(status)
            await self.send(m)

            # Compra proativa se SoC muito baixo quando há rodada ativa
            R = getattr(self.agent, "active_round_id", None)
            now = time.time()
            if self.agent.soc_kwh < 0.1 * self.agent.cap_kwh and R and now <= self.agent.round_deadline_ts:
                need = max(0.0, 0.25 * self.agent.cap_kwh)
                req = {"round_id": R, "need_kw": need, "price_max": self.agent.price_max, "t": now}
                r = Message(to=self.agent.grid_node_jid)
                r.metadata = {"performative": "request", "type": "energy_request"}
                r.body = json.dumps(req)
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
            if now <= self.agent.round_deadline_ts and self.agent.soc_kwh > 0.2 * self.agent.cap_kwh:
                sell_kw = max(0.0, 0.10 * self.agent.cap_kwh)  # política simples
                off = {"round_id": self.agent.active_round_id, "offer_kw": sell_kw, "price": self.agent.ask_price, "t": now}
                o = Message(to=self.agent.grid_node_jid)
                o.metadata = {"performative": "propose", "type": "energy_offer"}
                o.body = json.dumps(off)
                await self.send(o)

    class StartAfterDelay(OneShotBehaviour):
        def __init__(self, delay_s=30):
            super().__init__()
            self.delay_s = delay_s
        async def run(self):
            await asyncio.sleep(self.delay_s)
            self.agent.add_behaviour(self.agent.Monitor(period=30))

    async def setup(self):
        jid_local = str(self.jid).split('@')[0]
        print(f"[{jid_local}] Storage Manager Agent starting...")
        self.add_behaviour(self.StartAfterDelay(delay_s=30))
        self.add_behaviour(self.InviteReceiver())
