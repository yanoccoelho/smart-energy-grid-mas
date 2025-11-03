# agents/household_agent.py
import time
import json
import random
import spade
import asyncio
from datetime import datetime
from spade.behaviour import PeriodicBehaviour, CyclicBehaviour, OneShotBehaviour
from spade.message import Message

class HouseholdAgent(spade.agent.Agent):
    """
    Household/Prosumer: calcula demanda/produção, opera bateria, envia status
    e participa do mercado; ao receber CFP, envia imediatamente pedido (déficit)
    ou oferta (excedente), além do envio periódico de status para o Grid.
    """

    def __init__(self, jid, password, grid_node_jid, is_prosumer=False, price_max=0.25, ask_price=0.20):
        super().__init__(jid, password)
        self.grid_node_jid = grid_node_jid
        self.is_prosumer = is_prosumer
        self.price_max = float(price_max)
        self.ask_price = float(ask_price)

        # Estado elétrico
        self.current_demand_kw = 0.0
        self.current_production_kw = 0.0
        self.battery_kwh = 0.0
        self.battery_capacity_kwh = 10.0 if is_prosumer else 0.0
        self.max_charge_kw = 3.0
        self.max_discharge_kw = 3.0

        # Rodada ativa (preenchida ao receber CFP)
        self.active_round_id = None
        self.round_deadline_ts = 0.0

    # --- Behaviours ---

    class RoundReceiver(CyclicBehaviour):
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
            # Lance imediato com base no estado atual
            self.agent.add_behaviour(self.agent.QuickBid())

    class QuickBid(OneShotBehaviour):
        async def run(self):
            R = getattr(self.agent, "active_round_id", None)
            now = time.time()
            if not R or now > self.agent.round_deadline_ts:
                return
            # Usa o estado corrente para decidir (sem esperar próximo update periódico)
            net_kw = self.agent.current_production_kw - self.agent.current_demand_kw
            jid_local = str(self.agent.jid).split('@')[0]
            if net_kw < -1e-6:
                need = -net_kw
                req = {"round_id": R, "need_kw": need, "price_max": self.agent.price_max, "t": now}
                r = Message(to=self.agent.grid_node_jid)
                r.metadata = {"performative": "request", "type": "energy_request"}
                r.body = json.dumps(req)
                await self.send(r)
                print(f"[{jid_local}] (QuickBid) Deficit {need:.2f} kW -> request sent (round {R})")
            elif net_kw > 1e-6:
                off = {"round_id": R, "offer_kw": net_kw, "price": self.agent.ask_price, "t": now}
                o = Message(to=self.agent.grid_node_jid)
                o.metadata = {"performative": "propose", "type": "energy_offer"}
                o.body = json.dumps(off)
                await self.send(o)
                print(f"[{jid_local}] (QuickBid) Surplus {net_kw:.2f} kW -> offer sent (round {R})")

    class UpdateStateBehaviour(PeriodicBehaviour):
        async def run(self):
            # Intervalo de atualização local (mantém telemetria viva para o Grid)
            period_s = self.period.total_seconds() if hasattr(self.period, "total_seconds") else float(self.period)
            period_s = max(1.0, period_s)
            dt_h = period_s / 3600.0

            # Consumo por faixa horária (simples)
            current_hour = datetime.now().hour
            if 0 <= current_hour < 6:
                self.agent.current_demand_kw = random.uniform(0.5, 1.5)
            elif 6 <= current_hour <= 9:
                self.agent.current_demand_kw = random.uniform(2.0, 4.0)
            elif 18 <= current_hour <= 22:
                self.agent.current_demand_kw = random.uniform(3.0, 5.0)
            else:
                self.agent.current_demand_kw = random.uniform(1.0, 2.5)

            # Produção (prosumer)
            if self.agent.is_prosumer:
                if 7 <= current_hour <= 19:
                    peak = 13
                    factor = max(0.0, 1 - (abs(current_hour - peak) / 6) ** 2)
                    self.agent.current_production_kw = factor * 5.0 + random.uniform(-0.5, 0.5)
                else:
                    self.agent.current_production_kw = 0.0
            else:
                self.agent.current_production_kw = 0.0

            # Bateria priorizando autoconsumo
            net_kw = self.agent.current_production_kw - self.agent.current_demand_kw
            if self.agent.battery_capacity_kwh > 0.0:
                if net_kw > 0:
                    max_space_kwh = max(0.0, self.agent.battery_capacity_kwh - self.agent.battery_kwh)
                    max_charge_kw_by_space = (max_space_kwh / dt_h) if dt_h > 0 else 0.0
                    charge_kw = min(net_kw, self.agent.max_charge_kw, max_charge_kw_by_space)
                    self.agent.battery_kwh += charge_kw * dt_h
                    net_kw -= charge_kw
                elif net_kw < 0:
                    need_kw = -net_kw
                    max_discharge_kw_by_soc = (self.agent.battery_kwh / dt_h) if dt_h > 0 else 0.0
                    discharge_kw = min(need_kw, self.agent.max_discharge_kw, max_discharge_kw_by_soc)
                    self.agent.battery_kwh -= discharge_kw * dt_h
                    net_kw += discharge_kw

            # Saldos e status
            excedente_kw = max(0.0, self.agent.current_production_kw - self.agent.current_demand_kw)
            demanda_kw   = max(0.0, self.agent.current_demand_kw - self.agent.current_production_kw)
            jid_local = str(self.agent.jid).split('@')[0]
            print(f"[{jid_local}] Demand={self.agent.current_demand_kw:.2f} kW | Prod={self.agent.current_production_kw:.2f} kW | SoC={self.agent.battery_kwh:.2f} kWh | Excess={excedente_kw:.2f} kW | DemandBal={demanda_kw:.2f} kW")

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

            # Se houver rodada ativa, reforça o lance baseado no estado periodicamente (sem depender só do QuickBid)
            R = getattr(self.agent, "active_round_id", None)
            now = time.time()
            if not R or now > self.agent.round_deadline_ts:
                return
            if net_kw < -1e-6:
                need = -net_kw
                req = {"round_id": R, "need_kw": need, "price_max": self.agent.price_max, "t": now}
                r = Message(to=self.agent.grid_node_jid)
                r.metadata = {"performative": "request", "type": "energy_request"}
                r.body = json.dumps(req)
                await self.send(r)
                print(f"[{jid_local}] Deficit {need:.2f} kW -> sending request (round {R})")
            elif net_kw > 1e-6:
                off = {"round_id": R, "offer_kw": net_kw, "price": self.agent.ask_price, "t": now}
                o = Message(to=self.agent.grid_node_jid)
                o.metadata = {"performative": "propose", "type": "energy_offer"}
                o.body = json.dumps(off)
                await self.send(o)
                print(f"[{jid_local}] Surplus {net_kw:.2f} kW -> sending offer (round {R})")

    class ControlReceiver(CyclicBehaviour):
        async def run(self):
            msg = await self.receive(timeout=0.5)
            if not msg: return
            if (msg.metadata or {}).get("type") != "control_command": return
            # Aqui poderia aplicar efeitos locais do despacho (ex.: atualizar bateria/carga)

    class StartAfterDelay(OneShotBehaviour):
        def __init__(self, delay_s=30):
            super().__init__()
            self.delay_s = delay_s
        async def run(self):
            await asyncio.sleep(self.delay_s)
            self.agent.add_behaviour(self.agent.UpdateStateBehaviour(period=30))

    async def setup(self):
        jid_local = str(self.jid).split('@')[0]
        print(f"[{jid_local}] Household Agent starting...")
        self.add_behaviour(self.StartAfterDelay(delay_s=30))
        self.add_behaviour(self.ControlReceiver())
        self.add_behaviour(self.RoundReceiver())
