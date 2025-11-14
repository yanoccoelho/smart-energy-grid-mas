import time
import json
import random
import spade
import asyncio
from collections import defaultdict
from spade.behaviour import CyclicBehaviour, OneShotBehaviour
from spade.message import Message
from logs.db_logger import DBLogger
from agents.performance_metrics import PerformanceTracker


class GridNodeAgent(spade.agent.Agent):
    """Grid Node Agent - Market Coordinator with Failure Management"""

    def __init__(self, jid, password, expected_agents, env_jid, external_grid_config=None):
        super().__init__(jid, password)
        self.expected_agents = expected_agents
        self.env_jid = env_jid

        if external_grid_config is None:
            external_grid_config = {
                "enabled": True,
                "buy_price_min": 0.10,
                "buy_price_max": 0.15,
                "sell_price_min": 0.25,
                "sell_price_max": 0.32,
                "acceptance_prob": 1.0
            }

        self.external_grid_enabled = external_grid_config.get("enabled", True)
        self.external_grid_buy_price_min = external_grid_config.get("buy_price_min", 0.10)
        self.external_grid_buy_price_max = external_grid_config.get("buy_price_max", 0.15)
        self.external_grid_sell_price_min = external_grid_config.get("sell_price_min", 0.25)
        self.external_grid_sell_price_max = external_grid_config.get("sell_price_max", 0.32)
        self.external_grid_acceptance_prob = external_grid_config.get("acceptance_prob", 1.0)
        self.external_grid_buy_price = 0.0
        self.external_grid_sell_price = 0.0
        self.ext_grid_total_bought_kwh = 0.0
        self.ext_grid_total_sold_kwh = 0.0
        self.ext_grid_revenue = 0.0
        self.ext_grid_costs = 0.0
        self.ext_grid_rounds_available = 0
        self.ext_grid_rounds_unavailable = 0

        self.producer_failure_probability = 0.2
        self.any_producer_failed = False
        self.performance_tracker = PerformanceTracker()


    async def setup(self):
        self.db_logger = DBLogger()
        self.households_state = {}
        self.producers_state = {}
        self.storage_state = {}
        self.round_id = None
        self.round_phase = {}
        self.round_start_ts = 0.0
        self.round_deadline_ts = 0.0
        self.simulation_start_ts = time.time()
        self.known_households = set()
        self.known_producers = set()
        self.known_storage = set()
        self.status_seen_round = defaultdict(set)
        self.status_grace_s = 2.0
        self.offers_round = defaultdict(dict)
        self.requests_round = defaultdict(dict)
        self.invited_round = defaultdict(set)
        self.declined_round = defaultdict(set)
        self.auction_log = []
        self.totals_round = defaultdict(lambda: {"demand_kwh": 0.0, "available_kwh": 0.0})
        self.counts_round = defaultdict(lambda: {"buyers": 0, "sellers": 0, "declined": 0})
        self.sim_hour = 7
        self.sim_day = 1
        self.round_counter = 1
        self.current_solar = 0.0
        self.current_wind = 0.0
        self.current_temp = 20.0

        self.add_behaviour(self.Receiver())
        self.add_behaviour(self.StartupCoordinator())

    def _add_event(self, event_type, agent_jid, data, price=None, round_id=None):
        evt = {
            "ts": time.time(),
            "event": event_type,
            "agent": str(agent_jid),
            "data": data,
            "price": price,
            "round_id": round_id
        }
        self.auction_log.append(evt)

    def _get_demand_period(self, hour):
        if 6 <= hour < 9:
            return "Alta Demanda - Pico Matinal"
        elif 18 <= hour < 22:
            return "Alta Demanda - Pico Noturno"
        elif 0 <= hour < 6:
            return "Baixa Demanda - Madrugada"
        else:
            return "Demanda Média - Período Diurno"

    def _check_and_trigger_failure(self):
        """Check if storage is full and trigger ONE failure if conditions met."""
        storage_full = False
        for s_jid, state in self.storage_state.items():
            soc = state.get("soc_kwh", 0)
            cap = state.get("cap_kwh", 1)
            if soc >= cap * 0.99:
                storage_full = True
                break

        if not storage_full:
            return

        # ✅ CORREÇÃO: Sempre atualizar flag baseado no estado real
        self.any_producer_failed = False
        for p_jid, state in self.producers_state.items():
            if not state.get("is_operational", True):
                self.any_producer_failed = True
                break

        # Se já tem produtor falhado, não criar nova falha
        if self.any_producer_failed:
            return

        # Tentar criar uma nova falha
        for p_jid, state in self.producers_state.items():
            if state.get("is_operational", True):
                if random.random() < self.producer_failure_probability:
                    failure_duration = random.randint(1, 4)
                    state["is_operational"] = False
                    state["failure_rounds_remaining"] = failure_duration
                    state["failure_rounds_total"] = failure_duration
                    state["prod_kwh"] = 0.0
                    print(f"\n⚠️  SYSTEM ALERT: {p_jid} FAILED! (Offline for {failure_duration} rounds)")
                    print(f"🔋 Emergency backup activated: Storage will cover the deficit\n")
                    self.any_producer_failed = True
                    break

    class Receiver(CyclicBehaviour):
        async def run(self):
            msg = await self.receive(timeout=0.5)
            if not msg:
                return

            sender = str(msg.sender).split("/")[0]
            msg_type = msg.metadata.get("type", "")

            if msg_type == "register_household":
                self.agent.known_households.add(sender)
                self.agent._add_event("register", sender, {"type": "household"})
                return
            elif msg_type == "register_producer":
                self.agent.known_producers.add(sender)
                self.agent._add_event("register", sender, {"type": "producer"})
                return
            elif msg_type == "register_storage":
                self.agent.known_storage.add(sender)
                self.agent._add_event("register", sender, {"type": "storage"})
                return
            elif msg_type == "status_report":
                data = json.loads(msg.body)
                self.agent.households_state[sender] = data
                R = self.agent.round_id
                if R:
                    self.agent.status_seen_round[R].add(sender)
                self.agent._add_event("status", sender, data)
                self.agent.current_solar = data.get("solar_irradiance", self.agent.current_solar)
                self.agent.current_wind = data.get("wind_speed", self.agent.current_wind)
                self.agent.current_temp = data.get("temperature_c", self.agent.current_temp)
            elif msg_type == "production_report":
                data = json.loads(msg.body)
                
                # ✅ CORREÇÃO CRÍTICA: Preservar estado de falha gerenciado pelo GridNode
                if sender in self.agent.producers_state:
                    existing_state = self.agent.producers_state[sender]
                    
                    # Se o GridNode marcou como offline, manter offline até recuperação
                    if not existing_state.get("is_operational", True):
                        # Decrementar o contador aqui no GridNode
                        remaining = existing_state.get("failure_rounds_remaining", 0)
                        if remaining > 0:
                            remaining -= 1
                            existing_state["failure_rounds_remaining"] = remaining
                            
                            # Se chegou a zero, recuperar
                            if remaining == 0:
                                existing_state["is_operational"] = True
                                data["is_operational"] = True
                                data["failure_rounds_remaining"] = 0
                                # Permitir produção normal
                                print(f"\n✅ {sender} RECOVERED after failure!\n")
                            else:
                                # Ainda offline, forçar produção zero
                                data["is_operational"] = False
                                data["failure_rounds_remaining"] = remaining
                                data["failure_rounds_total"] = existing_state.get("failure_rounds_total", 0)
                                data["prod_kwh"] = 0.0
                        else:
                            # Contador já zerou
                            existing_state["is_operational"] = True
                            data["is_operational"] = True
                
                self.agent.producers_state[sender] = data
                
                # ✅ CORREÇÃO FINAL: Atualizar flag any_producer_failed após processar
                self.agent.any_producer_failed = False
                for p_jid, state in self.agent.producers_state.items():
                    if not state.get("is_operational", True):
                        self.agent.any_producer_failed = True
                        break
                
                R = self.agent.round_id
                if R:
                    self.agent.status_seen_round[R].add(sender)
                self.agent._add_event("production", sender, data)
                self.agent.current_solar = data.get("solar_irradiance", self.agent.current_solar)
                self.agent.current_wind = data.get("wind_speed", self.agent.current_wind)
                self.agent.current_temp = data.get("temperature_c", self.agent.current_temp)
            elif msg_type == "statusBattery":
                data = json.loads(msg.body)
                self.agent.storage_state[sender] = data
                R = self.agent.round_id
                if R:
                    self.agent.status_seen_round[R].add(sender)
                self.agent._add_event("battery_status", sender, data)
            elif msg_type == "energy_request":
                data = json.loads(msg.body)
                R = self.agent.round_id
                if data.get("round_id") != R:
                    return
                buyer = sender
                need_kwh = float(data.get("need_kwh", 0))
                price_max = float(data.get("price_max", 0))
                self.agent.requests_round[R][buyer] = {"need_kwh": need_kwh, "price_max": price_max}
                self.agent._add_event("request", buyer, need_kwh, price_max, R)
            elif msg_type == "energy_offer":
                data = json.loads(msg.body)
                rid = data.get("round_id")
                seller = sender
                offer = float(data.get("offer_kwh", 0))
                price = float(data.get("price", 0))
                now = time.time()
                R = self.agent.round_id

                if sender in self.agent.producers_state:
                    producer_state = self.agent.producers_state[sender]
                    if not producer_state.get("is_operational", True):
                        return

                if rid == R and self.agent.round_deadline_ts > 0.0 and now <= self.agent.round_deadline_ts:
                    self.agent.offers_round[R][seller] = {"offer_kwh": offer, "price": price, "ts": now}
                    self.agent._add_event("offer", seller, offer, price, R)
                else:
                    self.agent._add_event("late", seller, offer, price, rid)
            elif msg_type == "declined_offer":
                data = json.loads(msg.body)
                rid = data.get("round_id")
                R = self.agent.round_id
                if rid == R:
                    self.agent.declined_round[R].add(sender)
                    self.agent._add_event("declined", sender, {}, None, R)

    class StartupCoordinator(OneShotBehaviour):
        async def run(self):
            while True:
                await asyncio.sleep(0.2)

                got_h = len(self.agent.known_households)
                got_p = len(self.agent.known_producers)
                got_s = len(self.agent.known_storage)

                exp_h = self.agent.expected_agents["households"]
                exp_p = self.agent.expected_agents["producers"]
                exp_s = self.agent.expected_agents["storage"]

                if got_h >= exp_h and got_p >= exp_p and got_s >= exp_s:
                    break

            total = got_h + got_p + got_s
            print(f"[GridNode] ✅ All {total} agents registered!\n")

            if self.agent.external_grid_enabled:
                print(f"[GridNode] 🔌 External Grid enabled:")
                print(f"  - Buy price: €{self.agent.external_grid_buy_price_min:.2f}-€{self.agent.external_grid_buy_price_max:.2f}/kWh")
                print(f"  - Sell price: €{self.agent.external_grid_sell_price_min:.2f}-€{self.agent.external_grid_sell_price_max:.2f}/kWh")
                print(f"  - Availability: {self.agent.external_grid_acceptance_prob*100:.0f}%\n")

            print(f"[GridNode] Requesting initial environment update...")
            update_msg = Message(to=self.agent.env_jid)
            update_msg.metadata = {"performative": "request", "type": "request_environment_update"}
            update_msg.body = json.dumps({"command": "update", "sim_hour": self.agent.sim_hour})
            await self.send(update_msg)

            await asyncio.sleep(1.0)
            print(f"[GridNode] Waiting for initial status reports...\n")
            await asyncio.sleep(0.5)
            print(f"[GridNode] Starting auction system...\n")
            self.agent.add_behaviour(self.agent.RoundOrchestrator())

    class RoundOrchestrator(OneShotBehaviour):
        async def run(self):
            while True:
                R = time.time()
                self.agent.round_id = R
                self.agent.round_start_ts = R
                
                elapsed_real = R - self.agent.simulation_start_ts
                demand_period = self.agent._get_demand_period(self.agent.sim_hour)
                
                print("\n" + "=" * 80)
                print(f"  ROUND #{self.agent.round_counter}")
                print(f"  Simulated Time: Day {self.agent.sim_day} - {self.agent.sim_hour:02d}:00 ({demand_period})")
                print(f"  Real Time Elapsed: {elapsed_real:.1f}s")
                print("=" * 80)
                print(f"🌍 Environment: Solar {self.agent.current_solar:.2f} | Wind {self.agent.current_wind:.1f} m/s | Temp {self.agent.current_temp:.1f}°C\n")

                grace = self.agent.status_grace_s
                while True:
                    await asyncio.sleep(0.1)
                    expected = self.agent.known_households | self.agent.known_producers | self.agent.known_storage
                    got = self.agent.status_seen_round.get(R, set())
                    all_in = len(expected) > 0 and expected.issubset(got)
                    if all_in or (time.time() - self.agent.round_start_ts >= grace and len(got) > 0):
                        break

                self.agent._check_and_trigger_failure()

                print_status = self.agent.PrintAgentStatus()
                self.agent.add_behaviour(print_status)
                await asyncio.sleep(0.2)

                sellers = set()
                for p_jid, state in self.agent.producers_state.items():
                    prod = state.get("prod_kwh", 0)
                    operational = state.get("is_operational", True)
                    if prod > 0.01 and operational:
                        sellers.add(p_jid)

                for h_jid, state in self.agent.households_state.items():
                    prod_kwh = state.get("prod_kwh", 0)
                    demand_kwh = state.get("demand_kwh", 0)
                    if prod_kwh > demand_kwh:
                        sellers.add(h_jid)

                for s_jid, state in self.agent.storage_state.items():
                    soc = state.get("soc_kwh", 0)
                    cap = state.get("cap_kwh", 1)
                    soc_pct = (soc / cap * 100) if cap > 0 else 0
                    emergency_only = state.get("emergency_only", False)
                    
                    if emergency_only:
                        if self.agent.any_producer_failed and soc_pct > 20.0:
                            sellers.add(s_jid)
                    else:
                        if soc_pct >= 95.0:
                            avail = soc - 0.2 * cap
                            if avail > 0:
                                sellers.add(s_jid)

                self.agent.invited_round[R] = set(sellers)

                print_table = self.agent.PrintTotalsTable(R)
                self.agent.add_behaviour(print_table)
                await asyncio.sleep(0.2)
                
                real_buyers = set()
                for h_jid, state in self.agent.households_state.items():
                    demand = state.get("demand_kwh", 0)
                    prod = state.get("prod_kwh", 0)
                    if demand > prod:
                        real_buyers.add(h_jid)

                for s_jid, state in self.agent.storage_state.items():
                    soc = state.get("soc_kwh", 0)
                    cap = state.get("cap_kwh", 1)
                    soc_pct = (soc / cap * 100) if cap > 0 else 0
                    emergency_only = state.get("emergency_only", False)
                    
                    if emergency_only:
                        if soc_pct < 99.0 and not self.agent.any_producer_failed:
                            real_buyers.add(s_jid)
                    else:
                        if soc_pct < 95.0:
                            real_buyers.add(s_jid)

                num_potential_buyers = len(real_buyers)

                # ✅ CORREÇÃO: Enviar CFP apenas para sellers + buyers (não para produtores offline)
                eligible_for_cfp = sellers.copy()
                eligible_for_cfp.update(real_buyers)

                if len(eligible_for_cfp) > 0:
                    print(f"🔨 AUCTION PROCESS:\n")
                    print(f"→ Broadcasting CFP to eligible agents...")
                    print(f"  {len(sellers)} eligible sellers | {num_potential_buyers} potential buyers")
                    print(f"  Waiting for responses (10s deadline)...\n")
                    
                    self.agent.round_deadline_ts = time.time() + 10.0
                    burst = self.agent._InviteBurstSend(R, list(eligible_for_cfp), self.agent.round_deadline_ts, self.agent.any_producer_failed)
                    self.agent.add_behaviour(burst)
                    await asyncio.sleep(10.0)
                else:
                    print("⚠️  No agents available for auction.\n")

                offers = self.agent.offers_round.get(R, {})
                reqs = list(self.agent.requests_round.get(R, {}).items())
                declined = self.agent.declined_round.get(R, set())
                
                print(f"📥 OFFERS RECEIVED ({len(offers)} of {len(sellers)} invited):")
                for seller, offer_data in offers.items():
                    kwh = offer_data["offer_kwh"]
                    price = offer_data["price"]
                    print(f"  ✅ {seller}: {kwh:.1f} kWh @ €{price:.2f}/kWh")
                
                if len(declined) > 0:
                    print(f"\n🚫 NO RESPONSE ({len(declined)}):")
                    for agent_jid in declined:
                        print(f"  ⚠️ {agent_jid} (declined to participate)")
                
                print(f"\n🔍 MATCHING:\n")

                # ✅ Algoritmo de matching com compra parcial
                matched_count = 0
                partial_count = 0
                unmatched_count = 0
                total_traded = 0.0
                total_value = 0.0
                prices_paid = []
                matched_buyers = set()
                buyer_fulfillment = {}
                
                seller_remaining = {}
                for seller, offer_data in offers.items():
                    seller_remaining[seller] = offer_data["offer_kwh"]

                for buyer, req_data in reqs:
                    need_kwh = req_data["need_kwh"]
                    price_max = req_data["price_max"]
                    
                    available_sellers = []
                    for seller, offer_data in offers.items():
                        if seller_remaining[seller] > 0.01 and offer_data["price"] <= price_max:
                            available_sellers.append((offer_data["price"], seller, offer_data))
                    
                    if not available_sellers:
                        print(f"  ❌ {buyer} needs {need_kwh:.1f} kWh")
                        print(f"     → NO MATCH (no affordable sellers)\n")
                        unmatched_count += 1
                        buyer_fulfillment[buyer] = 0.0
                        continue
                    
                    available_sellers.sort()
                    
                    total_bought = 0.0
                    total_cost = 0.0
                    purchases = []
                    
                    for price, seller, offer_data in available_sellers:
                        available = seller_remaining[seller]
                        remaining_need = need_kwh - total_bought
                        
                        if remaining_need <= 0:
                            break
                        
                        if available >= remaining_need:
                            amount = remaining_need
                        else:
                            amount = available
                        
                        seller_remaining[seller] -= amount
                        total_bought += amount
                        cost = amount * price
                        total_cost += cost
                        purchases.append((seller, amount, price, cost))
                    
                    if total_bought > 0:
                        fulfillment_pct = (total_bought / need_kwh) * 100
                        buyer_fulfillment[buyer] = fulfillment_pct
                        
                        if fulfillment_pct >= 99.9:
                            print(f"  ✅ {buyer} needs {need_kwh:.1f} kWh")
                            matched_count += 1
                        else:
                            print(f"  ⚠️ {buyer} needs {need_kwh:.1f} kWh")
                            partial_count += 1
                        
                        for idx, (seller, amount, price, cost) in enumerate(purchases):
                            remaining_after = seller_remaining[seller]
                            seller_before = remaining_after + amount
                            
                            print(f"     → MATCHED with {seller} @ €{price:.2f}/kWh ({amount:.1f} kWh, €{cost:.2f})")
                            print(f"        {seller} remaining: {remaining_after:.1f} kWh (was {seller_before:.1f} kWh)")
                        
                        avg_price = total_cost / total_bought if total_bought > 0 else 0
                        print(f"     → {buyer} received {total_bought:.1f}/{need_kwh:.1f} kWh ({fulfillment_pct:.0f}% fulfilled)")
                        print(f"     → Total cost: €{total_cost:.2f} (avg: €{avg_price:.2f}/kWh)\n")
                        
                        for seller, amount, price, cost in purchases:
                            buyer_msg = Message(to=buyer)
                            buyer_msg.metadata = {"performative": "accept", "type": "control_command"}
                            buyer_msg.body = json.dumps({
                                "round_id": R,
                                "command": "energy_purchased",
                                "kw": amount,
                                "price": price,
                                "from": seller,
                                "partial": total_bought < need_kwh,
                                "total_received": total_bought,
                                "total_needed": need_kwh
                            })
                            await self.send(buyer_msg)
                        
                        for seller, amount, price, cost in purchases:
                            seller_msg = Message(to=seller)
                            seller_msg.metadata = {"performative": "accept", "type": "offer_accept"}
                            seller_msg.body = json.dumps({
                                "round_id": R,
                                "buyer": buyer,
                                "kw": amount,
                                "price": price
                            })
                            await self.send(seller_msg)
                        
                        matched_buyers.add(buyer)
                        total_traded += total_bought
                        total_value += total_cost
                        prices_paid.append(avg_price)
                        
                        self.agent._add_event("match", buyer, {
                            "sellers": [s for s, _, _, _ in purchases],
                            "kwh": total_bought,
                            "partial": total_bought < need_kwh
                        }, avg_price, R)
                    else:
                        print(f"  ❌ {buyer} needs {need_kwh:.1f} kWh")
                        print(f"     → NO MATCH\n")
                        unmatched_count += 1
                        buyer_fulfillment[buyer] = 0.0
                
                print(f"🎯 AUCTION RESULTS:")
                print(f"   {len(reqs)} buyers requested energy")
                if matched_count > 0:
                    print(f"   {matched_count} fully matched")
                if partial_count > 0:
                    print(f"   {partial_count} partially matched")
                if unmatched_count > 0:
                    print(f"   {unmatched_count} unmatched request(s)")
                if len(declined) > 0:
                    print(f"   {len(declined)} sellers declined")
                if total_traded > 0:
                    print(f"   Total energy traded: {total_traded:.1f} kWh")
                    print(f"   Total market value: €{total_value:.2f}")
                    avg_price = sum(prices_paid) / len(prices_paid) if prices_paid else 0
                    print(f"   Average price: €{avg_price:.2f}/kWh")
                # External Grid
                if self.agent.external_grid_enabled:
                    self.agent.external_grid_buy_price = random.uniform(
                        self.agent.external_grid_buy_price_min,
                        self.agent.external_grid_buy_price_max
                    )
                    self.agent.external_grid_sell_price = random.uniform(
                        self.agent.external_grid_sell_price_min,
                        self.agent.external_grid_sell_price_max
                    )
                    
                    ext_available = random.random() < self.agent.external_grid_acceptance_prob
                    
                    unmet_demand = []
                    for buyer, req_data in reqs:
                        fulfillment = buyer_fulfillment.get(buyer, 0.0)
                        if fulfillment < 100.0:
                            need_kwh = req_data["need_kwh"]
                            received = need_kwh * (fulfillment / 100.0)
                            remaining = need_kwh - received
                            price_max = req_data["price_max"]
                            unmet_demand.append((buyer, remaining, price_max, fulfillment))
                    
                    surplus_energy = {}
                    for seller, remaining in seller_remaining.items():
                        if remaining > 0.5:
                            if seller in self.agent.storage_state:
                                storage_info = self.agent.storage_state[seller]
                                if storage_info.get("emergency_only", False):
                                    continue
                            surplus_energy[seller] = remaining
                    
                    ext_sold_total = 0.0
                    ext_sold_value = 0.0
                    ext_bought_total = 0.0
                    ext_bought_value = 0.0
                    
                    if ext_available:
                        self.agent.ext_grid_rounds_available += 1
                        
                        if len(unmet_demand) > 0 or len(surplus_energy) > 0:
                            print(f"\n💰 EXTERNAL GRID AVAILABLE:")
                            print(f"   Buy: €{self.agent.external_grid_buy_price:.2f}/kWh | Sell: €{self.agent.external_grid_sell_price:.2f}/kWh\n")
                        
                        for buyer, remaining_need, price_max, current_fulfillment in unmet_demand:
                            if self.agent.external_grid_sell_price <= price_max:
                                total_cost = remaining_need * self.agent.external_grid_sell_price
                                
                                if current_fulfillment > 0:
                                    print(f"  ⚡ {buyer} buying additional {remaining_need:.1f} kWh from External Grid @ €{self.agent.external_grid_sell_price:.2f}/kWh")
                                    print(f"     (completing partially fulfilled order: was {current_fulfillment:.0f}%, now 100%)")
                                else:
                                    print(f"  ⚡ {buyer} buying {remaining_need:.1f} kWh from External Grid @ €{self.agent.external_grid_sell_price:.2f}/kWh")
                                
                                print(f"     Total cost: €{total_cost:.2f}")
                                
                                buyer_msg = Message(to=buyer)
                                buyer_msg.metadata = {"performative": "accept", "type": "control_command"}
                                buyer_msg.body = json.dumps({
                                    "round_id": R,
                                    "command": "energy_purchased",
                                    "kw": remaining_need,
                                    "price": self.agent.external_grid_sell_price,
                                    "from": "external_grid"
                                })
                                await self.send(buyer_msg)
                                
                                self.agent.ext_grid_total_sold_kwh += remaining_need
                                self.agent.ext_grid_revenue += total_cost
                                ext_sold_total += remaining_need
                                ext_sold_value += total_cost
                                
                                # Update fulfillment
                                buyer_fulfillment[buyer] = 100.0
                            else:
                                print(f"  ⚠️ {buyer} can't afford External Grid for remaining {remaining_need:.1f} kWh")
                                print(f"     (€{self.agent.external_grid_sell_price:.2f}/kWh > max €{price_max:.2f}/kWh)")
                        
                        for seller, surplus_kwh in surplus_energy.items():
                            total_revenue = surplus_kwh * self.agent.external_grid_buy_price
                            
                            print(f"  💵 {seller} selling {surplus_kwh:.1f} kWh to External Grid @ €{self.agent.external_grid_buy_price:.2f}/kWh")
                            print(f"     Total revenue: €{total_revenue:.2f}")
                            
                            seller_msg = Message(to=seller)
                            seller_msg.metadata = {"performative": "accept", "type": "offer_accept"}
                            seller_msg.body = json.dumps({
                                "round_id": R,
                                "buyer": "external_grid",
                                "kw": surplus_kwh,
                                "price": self.agent.external_grid_buy_price
                            })
                            await self.send(seller_msg)
                            
                            self.agent.ext_grid_total_bought_kwh += surplus_kwh
                            self.agent.ext_grid_costs += total_revenue
                            ext_bought_total += surplus_kwh
                            ext_bought_value += total_revenue
                        
                        if ext_sold_total > 0 or ext_bought_total > 0:
                            print(f"\n  📊 [External Grid Summary]")
                            if ext_sold_total > 0:
                                print(f"    Sold to microgrid: {ext_sold_total:.1f} kWh @ €{self.agent.external_grid_sell_price:.2f}/kWh = €{ext_sold_value:.2f}")
                            if ext_bought_total > 0:
                                print(f"    Bought from microgrid: {ext_bought_total:.1f} kWh @ €{self.agent.external_grid_buy_price:.2f}/kWh = €{ext_bought_value:.2f}")
                    
                    else:
                        self.agent.ext_grid_rounds_unavailable += 1
                        
                        if len(unmet_demand) > 0 or len(surplus_energy) > 0:
                            print(f"\n🚫 EXTERNAL GRID UNAVAILABLE:\n")
                            
                            if len(unmet_demand) > 0:
                                print(f"  ⚠️  UNMET DEMAND (potential blackout):")
                                for buyer, remaining, _, fulfillment in unmet_demand:
                                    if fulfillment > 0:
                                        print(f"      {buyer}: {remaining:.1f} kWh NOT SUPPLIED (only {fulfillment:.0f}% fulfilled)")
                                    else:
                                        print(f"      {buyer}: {remaining:.1f} kWh NOT SUPPLIED")
                            
                            if len(surplus_energy) > 0:
                                print(f"  ⚠️  WASTED SURPLUS (curtailed):")
                                for seller, surplus_kwh in surplus_energy.items():
                                    print(f"      {seller}: {surplus_kwh:.1f} kWh NOT SOLD")
                
                # ✅ PERFORMANCE METRICS: Coletar dados do round
                round_data = {
                    'total_demand': sum(req_data['need_kwh'] for _, req_data in reqs) if reqs else 0,
                    'total_supplied': total_traded + ext_sold_total,
                    'market_value': total_value + ext_sold_value,
                    'wasted_energy': sum(seller_remaining.values()),
                    'ext_grid_sold': ext_sold_total,
                    'ext_grid_bought': ext_bought_total,
                    'buyer_fulfillment': buyer_fulfillment.copy(),
                    'any_producer_failed': self.agent.any_producer_failed,
                    'emergency_used': self.agent.any_producer_failed,
                    # Valores monetários reais das transações com external grid
                    'ext_grid_sold_value': ext_bought_value,      # € recebido vendendo para external grid
                    'ext_grid_bought_value': ext_sold_value
                }
                
                # ✅ Registrar round (imprime relatório automaticamente a cada 5 rounds)
                self.agent.performance_tracker.record_round(self.agent.round_counter, round_data)
                
                for p_jid, state in self.agent.producers_state.items():
                    if not state.get("is_operational", True):
                        if state.get("failure_rounds_remaining", 0) == 0:
                            print(f"\n✅ {p_jid} RECOVERED!\n")
                
                print(f"\n⏳ Waiting 10 seconds before next round...")
                await asyncio.sleep(8.0)
                
                self.agent.round_counter += 1
                
                self.agent.sim_hour += 1
                if self.agent.sim_hour >= 24:
                    self.agent.sim_hour = 0
                    self.agent.sim_day += 1
                
                update_msg = Message(to=self.agent.env_jid)
                update_msg.metadata = {"performative": "request", "type": "request_environment_update"}
                update_msg.body = json.dumps({"command": "update", "sim_hour": self.agent.sim_hour})
                await self.send(update_msg)
                
                await asyncio.sleep(2.0)

                
    class PrintAgentStatus(OneShotBehaviour):
        async def run(self):
            print("📊 AGENT STATUS REPORTS:\n")
            
            consumers = []
            prosumers = []
            
            for jid, state in self.agent.households_state.items():
                is_prosumer = state.get("is_prosumer", False)
                if is_prosumer:
                    prosumers.append((jid, state))
                else:
                    consumers.append((jid, state))
            
            print("[CONSUMERS]")
            for jid, state in consumers:
                demand_raw = state.get("demand_kwh", 0)
                demand = round(demand_raw, 1)
                deficit = -demand
                print(f"  {jid}: Demand = {demand:.1f} kWh | Deficit = {deficit:.1f} kWh")
            
            print("\n[PROSUMERS]")
            for jid, state in prosumers:
                demand_raw = state.get("demand_kwh", 0)
                prod_raw = state.get("prod_kwh", 0)
                demand = round(demand_raw, 1)
                prod = round(prod_raw, 1)
                net = prod - demand
                status = "Surplus" if net > 0 else "Deficit"
                solar = state.get("solar_irradiance", 0)
                area = state.get("panel_area_m2", 0)
                
                print(f"  {jid}: Demand = {demand:.1f} kWh | Production = {prod:.1f} kWh | {status} = {net:+.1f} kWh")
                print(f"           Solar: {solar:.2f} | Area: {area:.1f} m² → {prod:.1f} kWh")
            
            print("\n[PRODUCERS]")
            for jid, state in self.agent.producers_state.items():
                prod_raw = state.get("prod_kwh", 0)
                prod = round(prod_raw, 1)
                prod_type = state.get("type", "unknown")
                solar = state.get("solar_irradiance", 0)
                wind = state.get("wind_speed", 0)
                is_operational = state.get("is_operational", True)
                failure_remaining = state.get("failure_rounds_remaining", 0)
                failure_total = state.get("failure_rounds_total", 0)
                
                if not is_operational:
                    current_round = failure_total - failure_remaining + 1
                    status = f"Offline - Round {current_round}/{failure_total}"
                    print(f"  {jid}: Production = {prod:.1f} kWh ({status}) ⚠️")
                else:
                    status = "Available" if prod > 0 else "Offline"
                    
                    if prod_type == "solar":
                        print(f"  {jid}: Production = {prod:.1f} kWh ({status})")
                        if prod > 0:
                            print(f"           Solar: {solar:.2f} × 20.0 (efficiency × capacity) = {prod:.1f} kWh")
                    elif prod_type == "wind":
                        if wind > 3.0:
                            if wind < 12.0:
                                power_fraction = (wind - 3.0) / 9.0
                            else:
                                power_fraction = 1.0
                        else:
                            power_fraction = 0.0
                        
                        print(f"  {jid}: Production = {prod:.1f} kWh ({status})")
                        if prod > 0:
                            print(f"           Wind: {wind:.1f} m/s → {power_fraction:.2f} × 50.0 kWh (capacity) = {prod:.1f} kWh")
                    else:
                        print(f"  {jid}: Production = {prod:.1f} kWh ({status})")
            
            print("\n[STORAGE]")
            for jid, state in self.agent.storage_state.items():
                soc_raw = state.get("soc_kwh", 0)
                cap_raw = state.get("cap_kwh", 1)
                soc = round(soc_raw, 1)
                cap = round(cap_raw, 1)
                pct = 100 * soc / cap if cap > 0 else 0
                avail = max(0, soc - 0.2 * cap)
                emergency_only = state.get("emergency_only", False)
                
                if emergency_only:
                    if self.agent.any_producer_failed:
                        print(f"  {jid}: SOC = {soc:.1f}/{cap:.1f} kWh ({pct:.0f}%) | Available: {avail:.1f} kWh 🔋")
                    else:
                        print(f"  {jid}: SOC = {soc:.1f}/{cap:.1f} kWh ({pct:.0f}%) | EMERGENCY RESERVE ⚡")
                else:
                    print(f"  {jid}: SOC = {soc:.1f}/{cap:.1f} kWh ({pct:.0f}%) | Available: {avail:.1f} kWh")
            
            print()

    class PrintTotalsTable(OneShotBehaviour):
        def __init__(self, round_id):
            super().__init__()
            self.round_id = round_id

        async def run(self):
            total_demand = 0.0
            total_available = 0.0
            num_buyers = 0
            num_sellers = 0

            for state in self.agent.households_state.values():
                demand = state.get("demand_kwh", 0)
                prod = state.get("prod_kwh", 0)
                if demand > prod:
                    total_demand += (demand - prod)
                    num_buyers += 1
                elif prod > demand:
                    total_available += (prod - demand)
                    num_sellers += 1

            for state in self.agent.producers_state.values():
                prod = state.get("prod_kwh", 0)
                if prod > 0 and state.get("is_operational", True):
                    total_available += prod
                    num_sellers += 1

            for state in self.agent.storage_state.values():
                soc = state.get("soc_kwh", 0)
                cap = state.get("cap_kwh", 1)
                soc_pct = (soc / cap * 100) if cap > 0 else 0
                emergency_only = state.get("emergency_only", False)
                
                if emergency_only:
                    if self.agent.any_producer_failed and soc_pct > 20.0:
                        avail = soc - 0.2 * cap
                        if avail > 0:
                            total_available += avail
                            num_sellers += 1
                    elif soc_pct < 99.0 and not self.agent.any_producer_failed:
                        need = cap - soc
                        if need > 0.5:
                            total_demand += need
                            num_buyers += 1
                else:
                    if soc_pct >= 95.0:
                        avail = soc - 0.2 * cap
                        if avail > 0:
                            total_available += avail
                            num_sellers += 1
                    else:
                        need = cap - soc
                        if need > 0:
                            total_demand += need
                            num_buyers += 1

            balance = total_available - total_demand
            status = "surplus" if balance >= 0 else "deficit"

            print("╔" + "=" * 58 + "╗")
            print("║" + " " * 10 + "GRID ENERGY MARKET - ROUND SUMMARY" + " " * 14 + "║")
            print("╠" + "=" * 58 + "╣")
            print(f"║  Total Demand:     {total_demand:7.1f} kWh  ({num_buyers} buyers)" + " " * (58 - 44 - len(str(num_buyers))) + "║")
            print(f"║  Total Available:  {total_available:7.1f} kWh  ({num_sellers} sellers)" + " " * (58 - 46 - len(str(num_sellers))) + "║")
            print(f"║  Market Balance:   {balance:+7.1f} kWh ({status})" + " " * (58 - 37 - len(status)) + "║")
            print("╚" + "=" * 58 + "╝\n")

    class _InviteBurstSend(OneShotBehaviour):
        def __init__(self, round_id, seller_jids, deadline_ts, producers_failed=False):
            super().__init__()
            self.round_id = round_id
            self.seller_jids = seller_jids
            self.deadline_ts = deadline_ts
            self.producers_failed = producers_failed

        async def run(self):
            for jid in self.seller_jids:
                msg = Message(to=jid)
                msg.metadata = {"performative": "cfp", "type": "call_for_offers"}
                msg.body = json.dumps({
                    "round_id": self.round_id,
                    "deadline_ts": self.deadline_ts,
                    "producers_failed": self.producers_failed
                })
                await self.send(msg)
