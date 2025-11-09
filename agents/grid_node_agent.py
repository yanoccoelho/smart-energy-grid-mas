import time
import json
import spade
import asyncio
from collections import defaultdict
from spade.behaviour import CyclicBehaviour, OneShotBehaviour
from spade.message import Message
from logs.db_logger import DBLogger


class GridNodeAgent(spade.agent.Agent):
    """
    The Grid Node Agent orchestrates the entire market process in stable cycles:
      1. Collects the latest status updates from all agents.
      2. Starts an auction: sends CFPs to eligible sellers and waits for 10 seconds.
      3. Resolves each energy request by selecting the lowest-price offer.
      4. Waits 10 seconds before starting a new round.
    """

    async def setup(self):
        """Initialize agent state and behaviors."""
        self.db_logger = DBLogger()

        # Last known states by agent type
        self.households_state = {}
        self.producers_state = {}
        self.storage_state = {}

        # Round control
        self.round_id = None
        self.round_phase = {}
        self.round_start_ts = 0.0
        self.round_deadline_ts = 0.0

        # Known agents
        self.known_households = set()
        self.known_producers = set()
        self.known_storage = set()
        self.status_seen_round = defaultdict(set)
        self.status_grace_s = 3.0  # tolerance for late reports

        # Auction structures
        self.invited_round = defaultdict(set)
        self.offers_round = defaultdict(dict)
        self.requests_round = defaultdict(list)
        self.auction_log = defaultdict(lambda: {"invited": set(), "responses": {}, "deadline": 0.0})

        # Statistics for reports
        self.totals_round = defaultdict(lambda: {
            "demand_kw_status": 0.0,
            "offer_kw_status": 0.0,
            "demand_kw_bids": 0.0,
            "offer_kw_bids": 0.0,
        })
        self.counts_round = defaultdict(lambda: {
            "request_status": set(),
            "offer_status": set(),
            "request_bids": set(),
            "offer_bids": set(),
        })

        # Internal events
        self.events = []
        self.last_event_seq = 0
        self.debounce_delay_s = 0.3

        self._log_print(f"[{str(self.jid).split('@')[0]}] Grid Node Agent initialized.")

        # Behaviors
        self.add_behaviour(self.Receiver())
        self.add_behaviour(self.RoundOrchestrator())

    # Helper methods

    def _add_event(self, kind, jid, kw=0.0, price=0.0, R=None):
        """Store a local event and persist it in the database."""
        event = {"t": time.time(), "kind": kind, "jid": jid,
                 "kw": float(kw), "price": float(price), "R": R}
        self.events.append(event)
        if hasattr(self, "db_logger"):
            self.db_logger.log_event(kind, jid, kw, price, R)

    def _log_print(self, msg):
        """Print timestamped messages."""
        now = time.strftime("%H:%M:%S")
        print(f"[{now}] {msg}")

    # CFP Sending

    class _InviteBurstSend(OneShotBehaviour):
        """Send CFPs (Call for Proposals) to all eligible sellers."""

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

    # Message Receiver

    class Receiver(CyclicBehaviour):
        """Receive and process messages from all agents."""

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

            # Household / Prosumer status
            if mtype == "status_report":
                jid = data.get("jid", str(msg.sender))
                self.agent.households_state[jid] = data
                self.agent.known_households.add(jid)
                if R is not None:
                    self.agent.status_seen_round[R].add(jid)
                    demand_kw = float(data.get("demand_kw", 0.0))
                    prod_kw = float(data.get("prod_kw", 0.0))
                    dem_bal = max(0.0, demand_kw - prod_kw)
                    exc_bal = max(0.0, prod_kw - demand_kw)
                    self.agent.totals_round[R]["demand_kw_status"] += dem_bal
                    self.agent.totals_round[R]["offer_kw_status"] += exc_bal
                    if dem_bal > 0:
                        self.agent.counts_round[R]["request_status"].add(jid)
                    if exc_bal > 0:
                        self.agent.counts_round[R]["offer_status"].add(jid)
                    self.agent._add_event("status", jid, 0.0, 0.0, R)
                return

            # Producer status
            if mtype == "production_report":
                jid = data.get("jid", str(msg.sender))
                self.agent.producers_state[jid] = data
                self.agent.known_producers.add(jid)
                if R is not None:
                    prod_kw = max(0.0, float(data.get("prod_kw", 0.0)))
                    self.agent.totals_round[R]["offer_kw_status"] += prod_kw
                    if prod_kw > 0:
                        self.agent.counts_round[R]["offer_status"].add(jid)
                    self.agent._add_event("status", jid, 0.0, 0.0, R)
                return

            # Storage status
            if mtype == "statusBattery":
                jid = data.get("jid", str(msg.sender))
                self.agent.storage_state[jid] = data
                self.agent.known_storage.add(jid)
                if R is not None:
                    self.agent.status_seen_round[R].add(jid)
                    self.agent._add_event("status", jid, 0.0, 0.0, R)
                return

            # Energy requests (buyers)
            if mtype == "energy_request":
                if data.get("round_id") != R:
                    return
                need = max(0.0, float(data.get("need_kw", 0.0)))
                pmax = float(data.get("price_max", float("inf")))
                buyer = str(msg.sender)
                self.agent.requests_round[R].append(
                    {"sender": buyer, "need_kw": need, "price_max": pmax, "t": now})
                self.agent.totals_round[R]["demand_kw_bids"] += need
                self.agent.counts_round[R]["request_bids"].add(buyer)
                self.agent._add_event("request", buyer, need, pmax, R)
                return

            # Energy offers (sellers)
            if mtype == "energy_offer":
                rid = data.get("round_id")
                offer = max(0.0, float(data.get("offer_kw", 0.0)))
                price = float(data.get("price", float("inf")))
                seller = str(msg.sender)
                if rid == R and self.agent.round_deadline_ts > 0.0 and now <= self.agent.round_deadline_ts:
                    self.agent.offers_round[R][seller] = {"kw": offer, "price": price, "t": now}
                    self.agent.auction_log[R]["responses"][seller] = {"kw": offer, "price": price}
                    self.agent.totals_round[R]["offer_kw_bids"] += offer
                    self.agent.counts_round[R]["offer_bids"].add(seller)
                    self.agent._add_event("offer", seller, offer, price, R)
                else:
                    self.agent._add_event("late", seller, offer, price, rid)
                return

    # Round Orchestrator

    class RoundOrchestrator(OneShotBehaviour):
        """Main loop that manages the market rounds."""

        async def run(self):
            while True:
                # Start new round
                self.agent.round_id = int(time.time())
                R = self.agent.round_id
                self.agent.round_phase[R] = "collect"
                self.agent.round_start_ts = time.time()
                self.agent.round_deadline_ts = 0.0

                # Reset structures
                self.agent.totals_round[R] = {
                    "demand_kw_status": 0.0,
                    "offer_kw_status": 0.0,
                    "demand_kw_bids": 0.0,
                    "offer_kw_bids": 0.0,
                }
                self.agent.counts_round[R] = {
                    "request_status": set(),
                    "offer_status": set(),
                    "request_bids": set(),
                    "offer_bids": set(),
                }
                self.agent.status_seen_round[R] = set()
                self.agent.offers_round[R] = {}
                self.agent.requests_round[R] = []
                self.agent.invited_round[R] = set()
                self.agent.auction_log[R] = {"invited": set(), "responses": {}, "deadline": 0.0}

                self.agent._log_print(f"[{str(self.agent.jid).split('@')[0]}] New round {R} started.")

                # Wait for status updates
                grace = self.agent.status_grace_s
                while True:
                    await asyncio.sleep(0.1)
                    expected = (
                        self.agent.known_households
                        | self.agent.known_producers
                        | self.agent.known_storage
                    )
                    got = self.agent.status_seen_round.get(R, set())
                    all_in = expected and expected.issubset(got)
                    if all_in or (time.time() - self.agent.round_start_ts >= grace and len(got) > 0):
                        break

                # Select eligible sellers
                sellers = set()
                for jid, st in self.agent.producers_state.items():
                    if float(st.get("prod_kw", 0.0)) > 0:
                        sellers.add(jid)
                for jid, st in self.agent.households_state.items():
                    d = float(st.get("demand_kw", 0.0))
                    p = float(st.get("prod_kw", 0.0))
                    if p > d:
                        sellers.add(jid)
                for jid, st in self.agent.storage_state.items():
                    soc = float(st.get("soc_kwh", 0.0))
                    cap = float(st.get("cap_kwh", 1e-9))
                    if soc > 0.2 * cap:
                        sellers.add(jid)

                # Send CFPs
                self.agent.invited_round[R] = set(sellers)
                self.agent.auction_log[R]["invited"] = set(sellers)
                self.agent.round_deadline_ts = time.time() + 10.0
                self.agent.auction_log[R]["deadline"] = self.agent.round_deadline_ts
                invite = {"round_id": R, "deadline_ts": self.agent.round_deadline_ts}
                self.agent.add_behaviour(self.agent._InviteBurstSend(R, invite, list(sellers)))

                # Wait for offers
                while time.time() <= self.agent.round_deadline_ts:
                    await asyncio.sleep(0.1)

                # Auction resolution
                offers = {
                    s: {"kw": rec["kw"], "price": rec["price"]}
                    for s, rec in self.agent.offers_round.get(R, {}).items()
                }
                reqs = list(self.agent.requests_round.get(R, []))
                print(f"\n--- Auction (round {R}) ---")
                for req in reqs:
                    buyer = req["sender"]
                    need = max(0.0, float(req["need_kw"]))
                    pmax = float(req.get("price_max", float("inf")))

                    cands = [
                        (sid, o["price"], o["kw"])
                        for sid, o in offers.items()
                        if o["price"] <= pmax and o["kw"] >= need
                    ]
                    if not cands:
                        continue

                    cands.sort(key=lambda x: (x[1], -x[2]))
                    winner_id, winner_price, _ = cands[0]

                    cm = {"round_id": R, "dispatch_kw": need, "price": winner_price, "t": time.time()}
                    mb = Message(to=buyer)
                    mb.metadata = {"performative": "accept", "type": "control_command"}
                    mb.body = json.dumps(cm)
                    await self.send(mb)

                    ack = {"round_id": R, "accepted_kw": need, "price": winner_price, "t": time.time()}
                    mv = Message(to=winner_id)
                    mv.metadata = {"performative": "accept", "type": "offer_accept"}
                    mv.body = json.dumps(ack)
                    await self.send(mv)

                    # Log auction result
                    self.agent._add_event("accept", f"{buyer}->{winner_id}", need, winner_price, R)
                    if hasattr(self.agent, "db_logger"):
                        self.agent.db_logger.log_auction(R, buyer, winner_id, need, winner_price)
                    offers[winner_id]["kw"] -= need

                print(f"--- End of auction (round {R}) ---\n")

                # Wait and print summary table before cleanup
                seq_snapshot = self.agent.last_event_seq
                print(f"[{time.strftime('%H:%M:%S')}] Printing grid summary for round {R}...")
                table_behaviour = self.agent.PrintTotalsTable(R, seq_snapshot)
                self.agent.add_behaviour(table_behaviour)

                # Wait enough time for PrintTotalsTable to execute
                await asyncio.sleep(self.agent.debounce_delay_s + 0.5)

                # Cleanup round data after printing
                print(f"[{time.strftime('%H:%M:%S')}] Cleaning up round {R} data...")
                for d in [
                    self.agent.offers_round,
                    self.agent.requests_round,
                    self.agent.invited_round,
                    self.agent.auction_log,
                    self.agent.totals_round,
                    self.agent.counts_round,
                    self.agent.status_seen_round,
                    self.agent.round_phase,
                ]:
                    d.pop(R, None)

                self.agent.round_id = None
                self.agent.round_deadline_ts = 0.0

                print(f"[{time.strftime('%H:%M:%S')}] Round {R} completed. Waiting for next cycle...\n")
    
                await asyncio.sleep(10.0)


    class PrintTotalsTable(OneShotBehaviour):
        """Prints a summary table of the current round's totals."""

        def __init__(self, R, seq_snapshot):
            super().__init__()
            self.R = R
            self.seq_snapshot = seq_snapshot

        async def run(self):
            # Wait a moment for all events to settle
            await asyncio.sleep(self.agent.debounce_delay_s)

            # Only skip if the round changed
            if self.agent.round_id != self.R:
                return

            ev = [e for e in self.agent.events if e.get("R") == self.R]

            def cnt_kind(k):
                return sum(1 for e in ev if e["kind"] == k)

            tot = self.agent.totals_round.get(self.R, {
                "demand_kw_status": 0.0,
                "offer_kw_status": 0.0,
                "demand_kw_bids": 0.0,
                "offer_kw_bids": 0.0
            })
            dem_total = tot["demand_kw_status"] + tot["demand_kw_bids"]
            off_total = tot["offer_kw_status"] + tot["offer_kw_bids"]

            csets = self.agent.counts_round.get(self.R, {
                "request_status": set(),
                "offer_status": set(),
                "request_bids": set(),
                "offer_bids": set()
            })
            req_agents = len(csets["request_status"] | csets["request_bids"])
            off_agents = len(csets["offer_status"] | csets["offer_bids"])

            print("\n------ Grid Table (round {}) ------".format(self.R))
            print("| Metric              | Count |     kW   |")
            print("|---------------------|-------|----------|")
            print(f"| Status messages     | {cnt_kind('status'):5d} | {0.0:8.2f} |")
            print(f"| Energy requests     | {req_agents:5d} | {dem_total:8.2f} |")
            print(f"| Energy offers       | {off_agents:5d} | {off_total:8.2f} |")
            print("-----------------------------------\n")

