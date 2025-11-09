import time
import json
import random
import spade
import asyncio
from datetime import datetime
from spade.behaviour import PeriodicBehaviour, CyclicBehaviour, OneShotBehaviour
from spade.message import Message
from logs.db_logger import DBLogger


class HouseholdAgent(spade.agent.Agent):
    """
    Household or Prosumer agent.
    - Periodically calculates demand, production, and battery operation.
    - Sends regular status updates to the Grid Node.
    - Participates in the market by submitting offers or requests during active rounds.
    """

    def __init__(self, jid, password, grid_node_jid, is_prosumer=False, price_max=0.25, ask_price=0.20):
        super().__init__(jid, password)
        self.grid_node_jid = grid_node_jid
        self.is_prosumer = is_prosumer
        self.price_max = float(price_max)
        self.ask_price = float(ask_price)

        # Electrical state
        self.current_demand_kw = 0.0
        self.current_production_kw = 0.0
        self.battery_kwh = 0.0
        self.battery_capacity_kwh = 10.0 if is_prosumer else 0.0
        self.max_charge_kw = 3.0
        self.max_discharge_kw = 3.0

        # Environmental factors (only relevant for prosumers)
        self.solar_irradiance = 0.0
        self.last_env_update = 0.0


        # Active auction round
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
    
    class RoundReceiver(CyclicBehaviour):
        """Waits for CFP messages and triggers an immediate bid."""

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
            jid_local = str(self.agent.jid).split('@')[0]
            self.agent._log_print(f"[{jid_local}] CFP received (round {self.agent.active_round_id})")
            self.agent._add_event("cfp_received", 0.0, 0.0, self.agent.active_round_id)

            # Immediate bid based on current state
            self.agent.add_behaviour(self.agent.QuickBid())

    class QuickBid(OneShotBehaviour):
        """Sends an immediate offer or request upon receiving a CFP."""

        async def run(self):
            R = getattr(self.agent, "active_round_id", None)
            now = time.time()
            if not R or now > self.agent.round_deadline_ts:
                return

            net_kw = self.agent.current_production_kw - self.agent.current_demand_kw
            jid_local = str(self.agent.jid).split('@')[0]

            if net_kw < -1e-6:
                # Energy deficit → send request
                need = -net_kw
                req = {"round_id": R, "need_kw": need, "price_max": self.agent.price_max, "t": now}
                r = Message(to=self.agent.grid_node_jid)
                r.metadata = {"performative": "request", "type": "energy_request"}
                r.body = json.dumps(req)
                await self.send(r)
                self.agent._add_event("quick_request", need, self.agent.price_max, R)

            elif net_kw > 1e-6:
                # Energy surplus → send offer
                off = {"round_id": R, "offer_kw": net_kw, "price": self.agent.ask_price, "t": now}
                o = Message(to=self.agent.grid_node_jid)
                o.metadata = {"performative": "propose", "type": "energy_offer"}
                o.body = json.dumps(off)
                await self.send(o)
                self.agent._add_event("quick_offer", net_kw, self.agent.ask_price, R)

    class UpdateStateBehaviour(PeriodicBehaviour):
        """Periodically updates consumption, production, battery, and sends status."""

        async def run(self):
            # Time step in hours
            period_s = self.period.total_seconds() if hasattr(self.period, "total_seconds") else float(self.period)
            dt_h = max(1.0, period_s) / 3600.0

            # Demand pattern by time of day
            current_hour = datetime.now().hour
            if 0 <= current_hour < 6:
                self.agent.current_demand_kw = random.uniform(0.5, 1.5)
            elif 6 <= current_hour <= 9:
                self.agent.current_demand_kw = random.uniform(2.0, 4.0)
            elif 18 <= current_hour <= 22:
                self.agent.current_demand_kw = random.uniform(3.0, 5.0)
            else:
                self.agent.current_demand_kw = random.uniform(1.0, 2.5)

            # Updated production: uses environment data if prosumer
            if self.agent.is_prosumer:
                irradiance = getattr(self.agent, "solar_irradiance", 0.0)
                # Simulate 5 kW max under full sun
                self.agent.current_production_kw = irradiance * 5.0 + random.uniform(-0.3, 0.3)
                self.agent.current_production_kw = max(0.0, self.agent.current_production_kw)
            else:
                self.agent.current_production_kw = 0.0


            # Battery operation
            net_kw = self.agent.current_production_kw - self.agent.current_demand_kw
            if self.agent.battery_capacity_kwh > 0.0:
                if net_kw > 0:  # charge
                    max_space_kwh = max(0.0, self.agent.battery_capacity_kwh - self.agent.battery_kwh)
                    charge_kw = min(net_kw, self.agent.max_charge_kw, max_space_kwh / dt_h)
                    self.agent.battery_kwh += charge_kw * dt_h
                    net_kw -= charge_kw
                elif net_kw < 0:  # discharge
                    need_kw = -net_kw
                    discharge_kw = min(need_kw, self.agent.max_discharge_kw, self.agent.battery_kwh / dt_h)
                    self.agent.battery_kwh -= discharge_kw * dt_h
                    net_kw += discharge_kw

            # Send status to Grid
            excedente_kw = max(0.0, self.agent.current_production_kw - self.agent.current_demand_kw)
            demanda_kw = max(0.0, self.agent.current_demand_kw - self.agent.current_production_kw)
            report = {
                "jid": str(self.agent.jid),
                "demand_kw": self.agent.current_demand_kw,
                "prod_kw": self.agent.current_production_kw,
                "soc_kwh": self.agent.battery_kwh,
                "cap_kwh": self.agent.battery_capacity_kwh,
                "excess_kw": excedente_kw,
                "demand_balance_kw": demanda_kw,
                "t": time.time(),
            }
            m = Message(to=self.agent.grid_node_jid)
            m.metadata = {"performative": "inform", "type": "status_report"}
            m.body = json.dumps(report)
            await self.send(m)
            self.agent._add_event("status", self.agent.current_demand_kw - self.agent.current_production_kw, 0.0)

            # If an auction is active, reinforce bid periodically
            R = getattr(self.agent, "active_round_id", None)
            now = time.time()
            if not R or now > self.agent.round_deadline_ts:
                return

            jid_local = str(self.agent.jid).split('@')[0]
            if net_kw < -1e-6:
                need = -net_kw
                req = {"round_id": R, "need_kw": need, "price_max": self.agent.price_max, "t": now}
                r = Message(to=self.agent.grid_node_jid)
                r.metadata = {"performative": "request", "type": "energy_request"}
                r.body = json.dumps(req)
                await self.send(r)
                self.agent._add_event("periodic_request", need, self.agent.price_max, R)

            elif net_kw > 1e-6:
                off = {"round_id": R, "offer_kw": net_kw, "price": self.agent.ask_price, "t": now}
                o = Message(to=self.agent.grid_node_jid)
                o.metadata = {"performative": "propose", "type": "energy_offer"}
                o.body = json.dumps(off)
                await self.send(o)
                self.agent._add_event("periodic_offer", net_kw, self.agent.ask_price, R)

    class ControlReceiver(CyclicBehaviour):
        """Receives control commands from the Grid Node."""

        async def run(self):
            msg = await self.receive(timeout=0.5)
            if not msg or (msg.metadata or {}).get("type") != "control_command":
                return
            self.agent._add_event("control_command", 0.0, 0.0)

    class StartAfterDelay(OneShotBehaviour):
        """Delays the start of periodic updates."""

        def __init__(self, delay_s=30):
            super().__init__()
            self.delay_s = delay_s

        async def run(self):
            await asyncio.sleep(self.delay_s)
            self.agent.add_behaviour(self.agent.UpdateStateBehaviour(period=30))
    
    class EnvironmentReceiver(CyclicBehaviour):
        """Receives periodic environmental updates from EnvironmentAgent."""

        async def run(self):
            msg = await self.receive(timeout=0.5)
            if not msg or (msg.metadata or {}).get("type") != "environment_update":
                return
            try:
                data = json.loads(msg.body)
            except Exception:
                return

            self.agent.solar_irradiance = float(data.get("solar_irradiance", 0.0))
            self.agent.last_env_update = time.time()
            jid_local = str(self.agent.jid).split('@')[0]
            self.agent._log_print(f"[{jid_local}] Environment update received: irradiance={self.agent.solar_irradiance:.2f}")
            self.agent._add_event("env_update", self.agent.solar_irradiance, 0.0)


    async def setup(self):
        jid_local = str(self.jid).split('@')[0]
        self._log_print(f"[{jid_local}] Household Agent started.")
        self.db_logger = DBLogger()
        self.add_behaviour(self.StartAfterDelay(delay_s=30))
        self.add_behaviour(self.ControlReceiver())
        self.add_behaviour(self.RoundReceiver())
        if self.is_prosumer:
            self.add_behaviour(self.EnvironmentReceiver())

