# agents/grid_node_agent.py
import time
import json
import spade
import asyncio
from collections import defaultdict
from spade.behaviour import CyclicBehaviour, OneShotBehaviour
from spade.message import Message

class GridNodeAgent(spade.agent.Agent):
    """
    Orquestrador por fases (ciclo estável):
      1) Coleta status de todos (por eventos), imprime tabela.
      2) Abre leilão: envia CFP apenas aos vendedores elegíveis e aguarda 10 s por ofertas.
      3) Resolve cada pedido com vencedor único (menor preço que cobre 100%), despacha.
      4) Pausa 10 s e reinicia.
    """

    async def setup(self):
        # Estados (últimos status por agente)
        self.households_state = {}    # jid -> status_report
        self.producers_state = {}     # jid -> production_report
        self.storage_state = {}       # jid -> statusBattery

        # Rodada/fases
        self.round_id = None
        self.round_phase = {}         # R -> "collect" | "auction"
        self.round_start_ts = 0.0
        self.round_deadline_ts = 0.0  # só >0 durante leilão de 10 s

        # Conjuntos "conhecidos" e barreira de status
        self.known_households = set()
        self.known_producers  = set()
        self.known_storage    = set()
        self.status_seen_round = defaultdict(set)  # R -> {jids que já enviaram status}
        self.status_grace_s = 3.0                  # tolerância para atraso de algum agente

        # Estruturas do leilão por rodada
        self.invited_round = defaultdict(set)      # R -> {jid}
        self.offers_round = defaultdict(dict)      # R -> {seller -> {"kw": float, "price": float, "t": ts}}
        self.requests_round = defaultdict(list)    # R -> [ {"sender": jid, "need_kw": q, "price_max": pmax, "t": ts} ]
        self.auction_log = defaultdict(lambda: {"invited": set(), "responses": {}, "deadline": 0.0})

        # Totais e contagens para a tabela
        self.totals_round = defaultdict(lambda: {
            "demand_kw_status": 0.0,
            "offer_kw_status":  0.0,
            "demand_kw_bids":   0.0,
            "offer_kw_bids":    0.0,
        })
        self.counts_round = defaultdict(lambda: {
            "request_status": set(),
            "offer_status":   set(),
            "request_bids":   set(),
            "offer_bids":     set(),
        })

        # Eventos de auditoria e debounce de impressão
        self.events = []  # {"t": ts, "kind": "...", "jid": str, "kw": float, "price": float, "R": int}
        self.last_event_seq = 0
        self.debounce_delay_s = 0.30

        print(f"[{str(self.jid).split('@')[0]}] Grid Node Agent starting and ready.")

        # Behaviours
        self.add_behaviour(self.Receiver())
        self.add_behaviour(self.RoundOrchestrator())  # loop sem timer de janela (usa barreira + sleeps de 10 s)

    # ---------- Helpers ----------
    def _add_event(self, kind, jid, kw=0.0, price=0.0, R=None):
        self.events.append({"t": time.time(), "kind": kind, "jid": jid, "kw": float(kw), "price": float(price), "R": R})

    class PrintTotalsTable(OneShotBehaviour):
        def __init__(self, R, seq_snapshot):
            super().__init__()
            self.R = R
            self.seq_snapshot = seq_snapshot
        async def run(self):
            await asyncio.sleep(self.agent.debounce_delay_s)
            if self.agent.round_id != self.R:
                return
            if self.agent.last_event_seq != self.seq_snapshot:
                return
            ev = [e for e in self.agent.events if e.get("R") == self.R]
            def cnt_kind(k): return sum(1 for e in ev if e["kind"] == k)
            tot = self.agent.totals_round.get(self.R, {
                "demand_kw_status":0.0,"offer_kw_status":0.0,"demand_kw_bids":0.0,"offer_kw_bids":0.0
            })
            dem_total = tot["demand_kw_status"] + tot["demand_kw_bids"]
            off_total = tot["offer_kw_status"]  + tot["offer_kw_bids"]
            csets = self.agent.counts_round.get(self.R, {
                "request_status":set(),"offer_status":set(),"request_bids":set(),"offer_bids":set()
            })
            req_agents = len(csets["request_status"] | csets["request_bids"])
            off_agents = len(csets["offer_status"]   | csets["offer_bids"])

            print("\n------ Grid Table (round {}) ------".format(self.R))
            print("| Metric              | Count |     kW   |")
            print("|---------------------|-------|----------|")
            print(f"| Status messages     | {cnt_kind('status'):5d} | {0.0:8.2f} |")
            print(f"| Energy requests     | {req_agents:5d} | {dem_total:8.2f} |")
            print(f"| Energy offers       | {off_agents:5d} | {off_total:8.2f} |")
            print("-----------------------------------\n")

    class _InviteBurstSend(OneShotBehaviour):
        def __init__(self, R, invite, sellers):
            super().__init__()
            self.R = R
            self.invite = invite
            self.sellers = sellers
        async def run(self):
            if self.agent.round_id != self.R:
                return
            for jid in self.sellers:
                m = Message(to=jid)
                m.metadata = {"performative": "cfp", "type": "call_for_offers"}
                m.body = json.dumps(self.invite)
                await self.send(m)
                self.agent._add_event("invite", jid, 0.0, 0.0, self.R)

    # ---------- Receiver de mensagens ----------
    class Receiver(CyclicBehaviour):
        async def run(self):
            msg = await self.receive(timeout=0.5)
            if not msg:
                return
            mtype = (msg.metadata or {}).get("type", "")
            try:
                data = json.loads(msg.body) if msg.body else {}
            except Exception:
                return

            now = time.time()
            R = self.agent.round_id

            # STATUS: Household/Prosumer
            if mtype == "status_report":
                jid = data.get("jid", str(msg.sender))
                self.agent.households_state[jid] = data
                self.agent.known_households.add(jid)
                if R is not None:
                    self.agent.status_seen_round[R].add(jid)
                    self.agent._add_event("status", jid, 0.0, 0.0, R)
                    demand_kw = float(data.get("demand_kw", 0.0))
                    prod_kw   = float(data.get("prod_kw", 0.0))
                    dem_bal = float(data.get("demand_balance_kw", max(0.0, demand_kw - prod_kw)))
                    exc_bal = float(data.get("excess_kw",          max(0.0, prod_kw   - demand_kw)))
                    self.agent.totals_round[R]["demand_kw_status"] += dem_bal
                    self.agent.totals_round[R]["offer_kw_status"]  += exc_bal
                    if dem_bal > 1e-9: self.agent.counts_round[R]["request_status"].add(jid)
                    if exc_bal > 1e-9: self.agent.counts_round[R]["offer_status"].add(jid)
                    print(f"[{str(self.agent.jid).split('@')[0]}] Received status from {jid}")
                    self.agent.last_event_seq += 1
                    self.agent.add_behaviour(self.agent.PrintTotalsTable(R, self.agent.last_event_seq))
                return

            # STATUS: Producer
            if mtype == "production_report":
                jid = data.get("jid", str(msg.sender))
                self.agent.producers_state[jid] = data
                self.agent.known_producers.add(jid)
                if R is not None:
                    self.agent.status_seen_round[R].add(jid)
                    self.agent._add_event("status", jid, 0.0, 0.0, R)
                    prod_kw = max(0.0, float(data.get("prod_kw", 0.0)))
                    self.agent.totals_round[R]["offer_kw_status"] += prod_kw
                    if prod_kw > 1e-9: self.agent.counts_round[R]["offer_status"].add(jid)
                    print(f"[{str(self.agent.jid).split('@')[0]}] Received production status from {jid}")
                    self.agent.last_event_seq += 1
                    self.agent.add_behaviour(self.agent.PrintTotalsTable(R, self.agent.last_event_seq))
                return

            # STATUS: Storage
            if mtype == "statusBattery":
                jid = data.get("jid", str(msg.sender))
                self.agent.storage_state[jid] = data
                self.agent.known_storage.add(jid)
                if R is not None:
                    self.agent.status_seen_round[R].add(jid)
                    self.agent._add_event("status", jid, 0.0, 0.0, R)
                    print(f"[{str(self.agent.jid).split('@')[0]}] Received battery status from {jid}")
                    self.agent.last_event_seq += 1
                    self.agent.add_behaviour(self.agent.PrintTotalsTable(R, self.agent.last_event_seq))
                return

            # BIDS: pedidos (buyers)
            if mtype == "energy_request":
                if data.get("round_id") != R:
                    return
                need = max(0.0, float(data.get("need_kw", 0.0)))
                pmax = float(data.get("price_max", float("inf")))
                buyer = str(msg.sender)
                self.agent.requests_round[R].append({"sender": buyer, "need_kw": need, "price_max": pmax, "t": now})
                self.agent._add_event("request", buyer, need, pmax, R)
                self.agent.totals_round[R]["demand_kw_bids"] += need
                self.agent.counts_round[R]["request_bids"].add(buyer)
                print(f"[{str(self.agent.jid).split('@')[0]}] Received request {need:.2f} kW from {buyer}")
                self.agent.last_event_seq += 1
                self.agent.add_behaviour(self.agent.PrintTotalsTable(R, self.agent.last_event_seq))
                return

            # BIDS: ofertas (sellers)
            if mtype == "energy_offer":
                rid = data.get("round_id")
                offer = max(0.0, float(data.get("offer_kw", 0.0)))
                price = float(data.get("price", float("inf")))
                seller = str(msg.sender)
                if rid == R and self.agent.round_deadline_ts > 0.0 and now <= self.agent.round_deadline_ts:
                    self.agent.offers_round[R][seller] = {"kw": offer, "price": price, "t": now}
                    self.agent.auction_log[R]["responses"][seller] = {"kw": offer, "price": price}
                    self.agent._add_event("offer", seller, offer, price, R)
                    self.agent.totals_round[R]["offer_kw_bids"] += offer
                    self.agent.counts_round[R]["offer_bids"].add(seller)
                    print(f"[{str(self.agent.jid).split('@')[0]}] Received offer {offer:.2f} kW @ {price:.4f} from {seller}")
                else:
                    self.agent._add_event("late", seller, offer, price, rid)
                self.agent.last_event_seq += 1
                self.agent.add_behaviour(self.agent.PrintTotalsTable(R, self.agent.last_event_seq))
                return

    # ---------- Orquestrador de rodada ----------
    class RoundOrchestrator(OneShotBehaviour):
        async def run(self):
            while True:
                # Abre nova rodada (fase collect)
                self.agent.round_id = int(time.time())
                R = self.agent.round_id
                self.agent.round_phase[R] = "collect"
                self.agent.round_start_ts = time.time()
                self.agent.round_deadline_ts = 0.0

                # Reset das estruturas da rodada
                self.agent.totals_round[R] = {"demand_kw_status":0.0,"offer_kw_status":0.0,"demand_kw_bids":0.0,"offer_kw_bids":0.0}
                self.agent.counts_round[R]  = {"request_status":set(),"offer_status":set(),"request_bids":set(),"offer_bids":set()}
                self.agent.status_seen_round[R] = set()
                self.agent.offers_round.pop(R, None); self.agent.offers_round[R] = {}
                self.agent.requests_round.pop(R, None); self.agent.requests_round[R] = []
                self.agent.invited_round.pop(R, None); self.agent.invited_round[R] = set()
                self.agent.auction_log.pop(R, None); self.agent.auction_log[R] = {"invited": set(), "responses": {}, "deadline": 0.0}
                self.agent.last_event_seq = 0

                print(f"[{str(self.agent.jid).split('@')[0]}] New round {R} opened; collecting statuses")

                # Espera barreira: todos conhecidos desta sessão
                grace = self.agent.status_grace_s
                while True:
                    await asyncio.sleep(0.1)
                    expected = self.agent.known_households | self.agent.known_producers | self.agent.known_storage
                    got = self.agent.status_seen_round.get(R, set())
                    all_in = expected and expected.issubset(got)
                    if all_in:
                        break
                    if time.time() - self.agent.round_start_ts >= grace and len(got) > 0:
                        break

                # Imprime tabela “após receber infos”
                self.agent.last_event_seq += 1
                self.agent.add_behaviour(self.agent.PrintTotalsTable(R, self.agent.last_event_seq))
                await asyncio.sleep(self.agent.debounce_delay_s + 0.05)

                # Seleciona vendedores e envia CFP; inicia deadline de 10 s (sem prints extras)
                sellers = set()
                for jid, st in self.agent.producers_state.items():
                    if max(0.0, float(st.get("prod_kw", 0.0))) > 1e-9: sellers.add(jid)
                for jid, st in self.agent.households_state.items():
                    d = float(st.get("demand_kw", 0.0)); p = float(st.get("prod_kw", 0.0))
                    if max(0.0, p - d) > 1e-9: sellers.add(jid)
                for jid, st in self.agent.storage_state.items():
                    soc = float(st.get("soc_kwh", 0.0)); cap = max(1e-9, float(st.get("cap_kwh", 0.0)))
                    if soc > 0.2 * cap: sellers.add(jid)

                self.agent.invited_round[R] = set(sellers)
                self.agent.auction_log[R]["invited"] = set(sellers)
                self.agent.round_deadline_ts = time.time() + 10.0
                self.agent.auction_log[R]["deadline"] = self.agent.round_deadline_ts

                invite = {"round_id": R, "deadline_ts": self.agent.round_deadline_ts, "note": "market_round"}
                self.agent.add_behaviour(self.agent._InviteBurstSend(R, invite, list(sellers)))

                # Aguarda fim da janela do leilão (10 s)
                while time.time() <= self.agent.round_deadline_ts:
                    await asyncio.sleep(0.1)

                # Resolve leilão por pedido: vencedor único que cobre 100% ao menor preço ≤ price_max
                offers = {s: {"kw": rec["kw"], "price": rec["price"]} for s, rec in self.agent.offers_round.get(R, {}).items()}
                reqs = list(self.agent.requests_round.get(R, []))
                print(f"\n--- Auction (round {R}) ---")
                for req in reqs:
                    buyer = req["sender"]
                    need  = max(0.0, float(req["need_kw"]))
                    pmax  = float(req.get("price_max", float("inf")))
                    print(f"> Buyer: {buyer} needs {need:.2f} kW (pmax {pmax:.4f})")
                    if offers:
                        print("  Offers received:")
                        for sid, o in sorted(offers.items(), key=lambda kv: kv[1]["price"]):
                            print(f"   - {sid}: avail={o['kw']:.2f} kW @ {o['price']:.4f}")
                    else:
                        print("  Offers received: none")
                    cands = [(sid, o["price"], o["kw"]) for sid, o in offers.items() if o["price"] <= pmax and o["kw"] + 1e-9 >= need]
                    if not cands:
                        print("  Winner: none (no seller covers full quantity within price_max)")
                        continue
                    cands.sort(key=lambda x: (x[1], -x[2]))
                    winner_id, winner_price, _ = cands[0]
                    print(f"  Winner: {winner_id} -> {need:.2f} kW @ {winner_price:.4f}")

                    cm = {"round_id": R, "dispatch_kw": need, "price": winner_price, "t": time.time()}
                    mb = Message(to=buyer); mb.metadata = {"performative": "accept", "type": "control_command"}; mb.body = json.dumps(cm)
                    await self.send(mb)
                    ack = {"round_id": R, "accepted_kw": need, "price": winner_price, "t": time.time()}
                    mv = Message(to=winner_id); mv.metadata = {"performative": "accept", "type": "offer_accept"}; mv.body = json.dumps(ack)
                    await self.send(mv)
                    self.agent._add_event("accept", f"{buyer}->{winner_id}", need, winner_price, R)
                    offers[winner_id]["kw"] -= need
                print("--- End auction ---\n")

                # Limpeza da rodada e pausa de 10 s entre transações
                self.agent.offers_round.pop(R, None)
                self.agent.requests_round.pop(R, None)
                self.agent.invited_round.pop(R, None)
                self.agent.auction_log.pop(R, None)
                self.agent.totals_round.pop(R, None)
                self.agent.counts_round.pop(R, None)
                self.agent.status_seen_round.pop(R, None)
                self.agent.round_phase.pop(R, None)
                self.agent.round_id = None
                self.agent.round_deadline_ts = 0.0

                await asyncio.sleep(10.0)  # espaçamento fixo entre ciclos
