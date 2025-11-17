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
from scenarios.base_config import SCENARIO_CONFIG


class GridNodeAgent(spade.agent.Agent):
    """
    GridNodeAgent acts as the central coordinator of the microgrid.

    It registers agents, orchestrates simulation rounds, collects status
    reports, runs the internal auction, manages producer failures, and
    interacts with the external grid when needed.

    Args:
        jid (str): XMPP JID of the GridNode agent.
        password (str): Password for authentication.
        expected_agents (dict): Expected number of agents by type. Example:
            {
                "households": 10,
                "producers": 3,
                "storage": 2
            }
        env_jid (str): JID of the EnvironmentAgent that provides
            environmental data (solar, wind, temperature).
        external_grid_config (dict, optional): Configuration for the external
            grid behavior. When None, values from SCENARIO_CONFIG are used.
        config (dict, optional): Simulation configuration dictionary.
            Defaults to SCENARIO_CONFIG.
    """

    def __init__(self, jid, password, expected_agents, env_jid,
                 external_grid_config=None, config=SCENARIO_CONFIG):
        super().__init__(jid, password)
        self.expected_agents = expected_agents
        self.env_jid = env_jid
        self.config = config
        self.transmission_limit_kw = self.config["SIMULATION"]["TRANSMISSION_LIMIT_KW"]

        if external_grid_config is None:
            external_grid_config = {
                "enabled": True,
                "buy_price_min": config["EXTERNAL_GRID"]["MIN_DYNAMIC_PRICE"],
                "buy_price_max": config["EXTERNAL_GRID"]["SELL_PRICE"],
                "sell_price_min": config["EXTERNAL_GRID"]["BUY_PRICE"],
                "sell_price_max": config["EXTERNAL_GRID"]["MAX_DYNAMIC_PRICE"],
                "acceptance_prob": config["EXTERNAL_GRID"]["ACCEPTANCE_PROB"],
            }

        # External grid configuration
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

        # Producer failure simulation
        self.producer_failure_probability = self.config["PRODUCERS"]["FAILURE_PROB"]
        self.any_producer_failed = False

        # Performance tracking
        self.performance_tracker = PerformanceTracker()

    async def setup(self):
        """
        Initialize runtime state and add initial behaviours.

        This method sets up internal structures for tracking agents, rounds,
        and auction data, and registers the Receiver and StartupCoordinator
        behaviours.
        """
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
        self.totals_round = defaultdict(
            lambda: {"demand_kwh": 0.0, "available_kwh": 0.0}
        )
        self.counts_round = defaultdict(
            lambda: {"buyers": 0, "sellers": 0, "declined": 0}
        )
        self.sim_hour = 7
        self.sim_day = 1
        self.round_counter = 1
        self.current_solar = 0.0
        self.current_wind = 0.0
        self.current_temp = 20.0

        self.add_behaviour(self.Receiver())
        self.add_behaviour(self.StartupCoordinator())

    def _add_event(self, event_type, agent_jid, data, price=None, round_id=None):
        """
        Append a market or system event to the internal auction log.

        Args:
            event_type (str): Type of event (e.g., 'offer', 'request', 'match').
            agent_jid (str): Agent JID associated with the event.
            data (Any): Additional event data payload.
            price (float, optional): Price associated with the event, if any.
            round_id (float, optional): Identifier of the round where the
                event took place.
        """
        evt = {
            "ts": time.time(),
            "event": event_type,
            "agent": str(agent_jid),
            "data": data,
            "price": price,
            "round_id": round_id,
        }
        self.auction_log.append(evt)

    def _get_demand_period(self, hour):
        """
        Map a simulated hour to a qualitative demand period label.

        Args:
            hour (int): Simulated hour (0–23).

        Returns:
            str: A demand description label.
        """
        if 6 <= hour < 9:
            return "High Demand - Morning Peak"
        elif 18 <= hour < 22:
            return "High Demand - Evening Peak"
        elif 0 <= hour < 6:
            return "Low Demand - Night Off-Peak"
        else:
            return "Medium Demand - Daytime"

    def _check_and_trigger_failure(self):
        """
        Check storage conditions and decide whether to trigger a producer failure.

        This method:
        - Detects when storage is nearly full.
        - Ensures only one producer is in failure at a time.
        - Randomly selects an operational producer to fail based on
          producer_failure_probability and assigns a failure duration.
        """
        storage_full = False
        for _, state in self.storage_state.items():
            soc = state.get("soc_kwh", 0)
            cap = state.get("cap_kwh", 1)
            if soc >= cap * 0.99:
                storage_full = True
                break

        if not storage_full:
            return

        # Recalculate flag based on actual producer state
        self.any_producer_failed = False
        for _, state in self.producers_state.items():
            if not state.get("is_operational", True):
                self.any_producer_failed = True
                break

        # If a producer is already failed, do not trigger a new failure
        if self.any_producer_failed:
            return

        # Try to create a new failure
        for p_jid, state in self.producers_state.items():
            if state.get("is_operational", True):
                if random.random() < self.producer_failure_probability:
                    min_rounds, max_rounds = self.config["PRODUCERS"]["FAILURE_ROUNDS_RANGE"]
                    failure_duration = random.randint(min_rounds, max_rounds)
                    state["is_operational"] = False
                    state["failure_rounds_remaining"] = failure_duration
                    state["failure_rounds_total"] = failure_duration
                    state["prod_kwh"] = 0.0
                    print(
                        f"\nSYSTEM ALERT: {p_jid} failed (offline for {failure_duration} rounds)."
                    )
                    print("Emergency backup activated: storage will cover the deficit.\n")
                    self.any_producer_failed = True
                    break

    class Receiver(CyclicBehaviour):
        """
        Behaviour responsible for receiving and routing all incoming messages.

        It handles:
        - Agent registration (households, producers, storage).
        - Status reports for demand, production, and storage.
        - Energy offers and requests.
        - Declined participation in auctions.
        """

        async def run(self):
            """
            Receive a single message (if available) and process it
            according to its type.
            """
            msg = await self.receive(timeout=0.5)
            if not msg:
                return

            sender = str(msg.sender).split("/")[0]
            msg_type = msg.metadata.get("type", "")

            if msg_type == "register_household":
                self.agent.known_households.add(sender)
                self.agent._add_event("register", sender, {"type": "household"})
                return

            if msg_type == "register_producer":
                self.agent.known_producers.add(sender)
                self.agent._add_event("register", sender, {"type": "producer"})
                return

            if msg_type == "register_storage":
                self.agent.known_storage.add(sender)
                self.agent._add_event("register", sender, {"type": "storage"})
                return

            if msg_type == "status_report":
                data = json.loads(msg.body)
                self.agent.households_state[sender] = data
                R = self.agent.round_id
                if R:
                    self.agent.status_seen_round[R].add(sender)
                self.agent._add_event("status", sender, data)
                self.agent.current_solar = data.get("solar_irradiance", self.agent.current_solar)
                self.agent.current_wind = data.get("wind_speed", self.agent.current_wind)
                self.agent.current_temp = data.get("temperature_c", self.agent.current_temp)
                return

            if msg_type == "production_report":
                data = json.loads(msg.body)

                # Preserve failure state controlled by the GridNode
                if sender in self.agent.producers_state:
                    existing_state = self.agent.producers_state[sender]

                    # If the GridNode marked this producer as offline, keep it offline
                    if not existing_state.get("is_operational", True):
                        remaining = existing_state.get("failure_rounds_remaining", 0)
                        if remaining > 0:
                            remaining -= 1
                            existing_state["failure_rounds_remaining"] = remaining

                            if remaining == 0:
                                existing_state["is_operational"] = True
                                data["is_operational"] = True
                                data["failure_rounds_remaining"] = 0
                                print(f"\n{sender} recovered after failure.\n")
                            else:
                                data["is_operational"] = False
                                data["failure_rounds_remaining"] = remaining
                                data["failure_rounds_total"] = existing_state.get(
                                    "failure_rounds_total", 0
                                )
                                data["prod_kwh"] = 0.0
                        else:
                            existing_state["is_operational"] = True
                            data["is_operational"] = True

                self.agent.producers_state[sender] = data

                # Update any_producer_failed flag based on all producers
                self.agent.any_producer_failed = False
                for _, state in self.agent.producers_state.items():
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
                return

            if msg_type == "statusBattery":
                data = json.loads(msg.body)
                self.agent.storage_state[sender] = data
                R = self.agent.round_id
                if R:
                    self.agent.status_seen_round[R].add(sender)
                self.agent._add_event("battery_status", sender, data)
                return

            if msg_type == "energy_request":
                data = json.loads(msg.body)
                R = self.agent.round_id
                if data.get("round_id") != R:
                    return
                buyer = sender
                need_kwh = float(data.get("need_kwh", 0))
                price_max = float(data.get("price_max", 0))
                self.agent.requests_round[R][buyer] = {
                    "need_kwh": need_kwh,
                    "price_max": price_max,
                }
                self.agent._add_event("request", buyer, need_kwh, price_max, R)
                return

            if msg_type == "energy_offer":
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

                if (
                    rid == R
                    and self.agent.round_deadline_ts > 0.0
                    and now <= self.agent.round_deadline_ts
                ):
                    self.agent.offers_round[R][seller] = {
                        "offer_kwh": offer,
                        "price": price,
                        "ts": now,
                    }
                    self.agent._add_event("offer", seller, offer, price, R)
                else:
                    self.agent._add_event("late", seller, offer, price, rid)
                return

            if msg_type == "declined_offer":
                data = json.loads(msg.body)
                rid = data.get("round_id")
                R = self.agent.round_id
                if rid == R:
                    self.agent.declined_round[R].add(sender)
                    self.agent._add_event("declined", sender, {}, None, R)

    class StartupCoordinator(OneShotBehaviour):
        """
        Behaviour that waits for all expected agents to register and
        then starts the simulation rounds.
        """

        async def run(self):
            """
            Wait until all expected agents (households, producers, storage)
            are registered, then request the first environment update and
            start the round orchestrator.
            """
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
            print(f"[GridNode] All {total} agents registered.\n")

            if self.agent.external_grid_enabled:
                print("[GridNode] External grid enabled:")
                print(
                    f"  - Buy price: €{self.agent.external_grid_buy_price_min:.2f}"
                    f"–€{self.agent.external_grid_buy_price_max:.2f}/kWh"
                )
                print(
                    f"  - Sell price: €{self.agent.external_grid_sell_price_min:.2f}"
                    f"–€{self.agent.external_grid_sell_price_max:.2f}/kWh"
                )
                print(
                    f"  - Availability: "
                    f"{self.agent.external_grid_acceptance_prob * 100:.0f}%\n"
                )

            print("[GridNode] Requesting initial environment update...")
            update_msg = Message(to=self.agent.env_jid)
            update_msg.metadata = {
                "performative": "request",
                "type": "request_environment_update",
            }
            update_msg.body = json.dumps(
                {"command": "update", "sim_hour": self.agent.sim_hour}
            )
            await self.send(update_msg)

            await asyncio.sleep(1.0)
            print("[GridNode] Waiting for initial status reports...\n")
            await asyncio.sleep(0.5)
            print("[GridNode] Starting auction system...\n")
            self.agent.add_behaviour(self.agent.RoundOrchestrator())

    class RoundOrchestrator(OneShotBehaviour):
        """
        Behaviour that continuously runs energy market rounds.

        Each loop corresponds to one simulation round:
        - Synchronizes status reports.
        - Classifies sellers and buyers.
        - Runs the auction and matching.
        - Optionally interacts with the external grid.
        - Updates performance metrics.
        - Advances simulation time and requests a new environment update.
        """

        async def run(self):
            """
            Execute the main simulation loop, performing repeated auction
            rounds until the agent is stopped.
            """
            while True:
                R = time.time()
                self.agent.round_id = R
                self.agent.round_start_ts = R

                elapsed_real = R - self.agent.simulation_start_ts
                demand_period = self.agent._get_demand_period(self.agent.sim_hour)

                print("\n" + "=" * 80)
                print(f"  ROUND #{self.agent.round_counter}")
                print(
                    f"  Simulated Time: Day {self.agent.sim_day} - "
                    f"{self.agent.sim_hour:02d}:00 ({demand_period})"
                )
                print(f"  Real Time Elapsed: {elapsed_real:.1f}s")
                print("=" * 80)
                print(
                    "Environment: "
                    f"Solar {self.agent.current_solar:.2f} | "
                    f"Wind {self.agent.current_wind:.1f} m/s | "
                    f"Temp {self.agent.current_temp:.1f}°C\n"
                )

                # Wait for status reports (or until grace time expires)
                grace = self.agent.status_grace_s
                while True:
                    await asyncio.sleep(0.1)
                    expected = (
                        self.agent.known_households
                        | self.agent.known_producers
                        | self.agent.known_storage
                    )
                    got = self.agent.status_seen_round.get(R, set())
                    all_in = len(expected) > 0 and expected.issubset(got)
                    if all_in or (
                        time.time() - self.agent.round_start_ts >= grace and len(got) > 0
                    ):
                        break

                # Check for potential producer failures
                self.agent._check_and_trigger_failure()

                # Print agent status snapshot
                print_status = self.agent.PrintAgentStatus()
                self.agent.add_behaviour(print_status)
                await asyncio.sleep(0.2)

                # Determine potential sellers
                sellers = set()

                # Producers
                for p_jid, state in self.agent.producers_state.items():
                    prod = state.get("prod_kwh", 0)
                    operational = state.get("is_operational", True)
                    if prod > 0.01 and operational:
                        sellers.add(p_jid)

                # Prosumers (households with surplus)
                for h_jid, state in self.agent.households_state.items():
                    prod_kwh = state.get("prod_kwh", 0)
                    demand_kwh = state.get("demand_kwh", 0)
                    if prod_kwh > demand_kwh:
                        sellers.add(h_jid)

                # Storage units as potential sellers
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

                # Print aggregate totals table
                print_table = self.agent.PrintTotalsTable(R)
                self.agent.add_behaviour(print_table)
                await asyncio.sleep(0.2)

                # Determine real buyers (households and storage)
                real_buyers = set()

                # Households needing energy
                for h_jid, state in self.agent.households_state.items():
                    demand = state.get("demand_kwh", 0)
                    prod = state.get("prod_kwh", 0)
                    if demand > prod:
                        real_buyers.add(h_jid)

                # Storage units needing energy
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

                # Send Call for Proposals only to eligible sellers and buyers
                eligible_for_cfp = sellers.copy()
                eligible_for_cfp.update(real_buyers)

                if len(eligible_for_cfp) > 0:
                    print("AUCTION PROCESS:\n")
                    print("→ Broadcasting Call for Proposals to eligible agents...")
                    print(
                        f"  {len(sellers)} eligible sellers | "
                        f"{num_potential_buyers} potential buyers"
                    )
                    offers_timeout = self.agent.config["SIMULATION"]["OFFERS_TIMEOUT"]
                    print(
                        f"  Waiting for responses "
                        f"({offers_timeout}s deadline)...\n"
                    )

                    self.agent.round_deadline_ts = time.time() + offers_timeout
                    burst = self.agent._InviteBurstSend(
                        R,
                        list(eligible_for_cfp),
                        self.agent.round_deadline_ts,
                        self.agent.any_producer_failed,
                    )
                    self.agent.add_behaviour(burst)
                    await asyncio.sleep(offers_timeout)
                else:
                    print("No agents available for auction.\n")

                # Collect offers and requests for this round
                offers = self.agent.offers_round.get(R, {})
                reqs = list(self.agent.requests_round.get(R, {}).items())
                req_lookup = dict(reqs)
                declined = self.agent.declined_round.get(R, set())

                print(f"OFFERS RECEIVED ({len(offers)} of {len(sellers)} invited):")
                for seller, offer_data in offers.items():
                    kwh = offer_data["offer_kwh"]
                    price = offer_data["price"]
                    print(f"  {seller}: {kwh:.1f} kWh @ €{price:.2f}/kWh")

                if len(declined) > 0:
                    print(f"\nNO RESPONSE ({len(declined)}):")
                    for agent_jid in declined:
                        print(f"  {agent_jid} (declined to participate)")

                print("\nMATCHING:\n")

                # Matching algorithm with partial allocation support
                matched_count = 0
                partial_count = 0
                unmatched_count = 0
                total_traded = 0.0
                total_value = 0.0
                prices_paid = []
                matched_buyers = set()
                buyer_fulfillment = {}
                buyer_received_kw = {buyer: 0.0 for buyer in req_lookup}

                seller_remaining = {}
                for seller, offer_data in offers.items():
                    seller_remaining[seller] = offer_data["offer_kwh"]

                for buyer, req_data in reqs:
                    need_kwh = req_data["need_kwh"]
                    price_max = req_data["price_max"]

                    # Sellers the buyer can afford
                    available_sellers = []
                    for seller, offer_data in offers.items():
                        if (
                            seller_remaining[seller] > 0.01
                            and offer_data["price"] <= price_max
                        ):
                            available_sellers.append(
                                (offer_data["price"], seller, offer_data)
                            )

                    if not available_sellers:
                        print(f"  {buyer} needs {need_kwh:.1f} kWh")
                        print("     → No match (no affordable sellers)\n")
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
                        remaining_limit = max(
                            0.0, self.agent.transmission_limit_kw - total_bought
                        )

                        if remaining_need <= 0 or remaining_limit <= 0:
                            break

                        intended_amount = min(available, remaining_need)
                        if intended_amount <= 0:
                            continue

                        amount = min(intended_amount, remaining_limit)
                        if amount <= 0:
                            break

                        if amount < intended_amount:
                            log_msg = (
                                "[TRANSMISSION LIMIT] Original offer of "
                                f"{intended_amount:.1f} kWh limited to "
                                f"{amount:.1f} kWh."
                            )
                            print(f"        {log_msg}")
                            self.agent._add_event(
                                "transmission_limit",
                                buyer,
                                {
                                    "seller": seller,
                                    "original_kwh": intended_amount,
                                    "delivered_kwh": amount,
                                },
                                price,
                                R,
                            )

                        seller_remaining[seller] -= amount
                        total_bought += amount
                        cost = amount * price
                        total_cost += cost
                        purchases.append((seller, amount, price, cost))

                    if total_bought > 0:
                        fulfillment_pct = (total_bought / need_kwh) * 100
                        buyer_received_kw[buyer] = total_bought
                        buyer_fulfillment[buyer] = fulfillment_pct

                        if fulfillment_pct >= 99.9:
                            print(f"  {buyer} needs {need_kwh:.1f} kWh")
                            matched_count += 1
                        else:
                            print(f"  {buyer} needs {need_kwh:.1f} kWh")
                            partial_count += 1

                        for _, (seller, amount, price, cost) in enumerate(purchases):
                            remaining_after = seller_remaining[seller]
                            seller_before = remaining_after + amount

                            print(
                                f"     → Matched with {seller} @ €{price:.2f}/kWh "
                                f"({amount:.1f} kWh, €{cost:.2f})"
                            )
                            print(
                                f"        {seller} remaining: "
                                f"{remaining_after:.1f} kWh "
                                f"(was {seller_before:.1f} kWh)"
                            )

                        avg_price = total_cost / total_bought if total_bought > 0 else 0
                        print(
                            f"     → {buyer} received {total_bought:.1f}/"
                            f"{need_kwh:.1f} kWh ({fulfillment_pct:.0f}% fulfilled)"
                        )
                        print(
                            f"     → Total cost: €{total_cost:.2f} "
                            f"(avg: €{avg_price:.2f}/kWh)\n"
                        )

                        # Notify buyer
                        for seller, amount, price, cost in purchases:
                            buyer_msg = Message(to=buyer)
                            buyer_msg.metadata = {
                                "performative": "accept",
                                "type": "control_command",
                            }
                            buyer_msg.body = json.dumps(
                                {
                                    "round_id": R,
                                    "command": "energy_purchased",
                                    "kw": amount,
                                    "price": price,
                                    "from": seller,
                                    "partial": total_bought < need_kwh,
                                    "total_received": total_bought,
                                    "total_needed": need_kwh,
                                }
                            )
                            await self.send(buyer_msg)

                        # Notify sellers
                        for seller, amount, price, cost in purchases:
                            seller_msg = Message(to=seller)
                            seller_msg.metadata = {
                                "performative": "accept",
                                "type": "offer_accept",
                            }
                            seller_msg.body = json.dumps(
                                {
                                    "round_id": R,
                                    "buyer": buyer,
                                    "kw": amount,
                                    "price": price,
                                }
                            )
                            await self.send(seller_msg)

                        matched_buyers.add(buyer)
                        total_traded += total_bought
                        total_value += total_cost
                        prices_paid.append(avg_price)

                        self.agent._add_event(
                            "match",
                            buyer,
                            {
                                "sellers": [s for s, _, _, _ in purchases],
                                "kwh": total_bought,
                                "partial": total_bought < need_kwh,
                            },
                            avg_price,
                            R,
                        )
                    else:
                        print(f"  {buyer} needs {need_kwh:.1f} kWh")
                        print("     → No match\n")
                        unmatched_count += 1
                        buyer_fulfillment[buyer] = 0.0

                print("AUCTION RESULTS:")
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
                    avg_price = (
                        sum(prices_paid) / len(prices_paid) if prices_paid else 0
                    )
                    print(f"   Average price: €{avg_price:.2f}/kWh")

                # External grid interaction
                if self.agent.external_grid_enabled:
                    self.agent.external_grid_buy_price = random.uniform(
                        self.agent.external_grid_buy_price_min,
                        self.agent.external_grid_buy_price_max,
                    )
                    self.agent.external_grid_sell_price = random.uniform(
                        self.agent.external_grid_sell_price_min,
                        self.agent.external_grid_sell_price_max,
                    )

                    ext_available = (
                        random.random() < self.agent.external_grid_acceptance_prob
                    )

                    # Unmet demand list
                    unmet_demand = []
                    for buyer, req_data in reqs:
                        need_kwh = req_data["need_kwh"]
                        received = buyer_received_kw.get(buyer, 0.0)
                        remaining = max(0.0, need_kwh - received)
                        fulfillment = (
                            (received / need_kwh * 100) if need_kwh > 0 else 0.0
                        )
                        buyer_fulfillment[buyer] = fulfillment
                        if remaining > 0.01:
                            price_max = req_data["price_max"]
                            unmet_demand.append(
                                (buyer, need_kwh, remaining, price_max, fulfillment)
                            )

                    # Surplus that could be sent to external grid
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
                            print("\nEXTERNAL GRID AVAILABLE:")
                            print(
                                f"   Buy: €{self.agent.external_grid_buy_price:.2f}/kWh | "
                                f"Sell: €{self.agent.external_grid_sell_price:.2f}/kWh\n"
                            )

                        # Serve unmet demand from external grid
                        for (
                            buyer,
                            need_kwh,
                            remaining_need,
                            price_max,
                            current_fulfillment,
                        ) in unmet_demand:
                            if self.agent.external_grid_sell_price <= price_max:
                                current_received = buyer_received_kw.get(buyer, 0.0)
                                remaining_limit = max(
                                    0.0,
                                    self.agent.transmission_limit_kw - current_received,
                                )

                                if remaining_limit <= 0:
                                    print(
                                        f"  {buyer} already at transmission limit "
                                        f"({self.agent.transmission_limit_kw:.1f} kWh). "
                                        "Skipping external supply."
                                    )
                                    continue

                                delivered = min(remaining_need, remaining_limit)
                                if delivered <= 0:
                                    continue

                                total_cost = (
                                    delivered * self.agent.external_grid_sell_price
                                )

                                if current_fulfillment > 0:
                                    print(
                                        f"  {buyer} buying additional "
                                        f"{delivered:.1f} kWh from external grid "
                                        f"@ €{self.agent.external_grid_sell_price:.2f}/kWh"
                                    )
                                else:
                                    print(
                                        f"  {buyer} buying {delivered:.1f} kWh from "
                                        "external grid "
                                        f"@ €{self.agent.external_grid_sell_price:.2f}/kWh"
                                    )

                                if delivered < remaining_need:
                                    log_msg = (
                                        "[TRANSMISSION LIMIT] Original demand of "
                                        f"{remaining_need:.1f} kWh limited to "
                                        f"{delivered:.1f} kWh."
                                    )
                                    print(f"     {log_msg}")
                                    self.agent._add_event(
                                        "transmission_limit",
                                        buyer,
                                        {
                                            "seller": "external_grid",
                                            "original_kwh": remaining_need,
                                            "delivered_kwh": delivered,
                                        },
                                        self.agent.external_grid_sell_price,
                                        R,
                                    )
                                else:
                                    print(
                                        "     Completing partially fulfilled order: "
                                        f"was {current_fulfillment:.0f}%, now 100%."
                                    )

                                print(f"     Total cost: €{total_cost:.2f}")

                                buyer_msg = Message(to=buyer)
                                buyer_msg.metadata = {
                                    "performative": "accept",
                                    "type": "control_command",
                                }
                                buyer_msg.body = json.dumps(
                                    {
                                        "round_id": R,
                                        "command": "energy_purchased",
                                        "kw": delivered,
                                        "price": self.agent.external_grid_sell_price,
                                        "from": "external_grid",
                                    }
                                )
                                await self.send(buyer_msg)

                                buyer_received_kw[buyer] = current_received + delivered

                                self.agent.ext_grid_total_sold_kwh += delivered
                                self.agent.ext_grid_revenue += total_cost
                                ext_sold_total += delivered
                                ext_sold_value += total_cost

                                # Update fulfillment
                                new_total = buyer_received_kw[buyer]
                                fulfillment_pct = (
                                    (new_total / need_kwh * 100)
                                    if need_kwh > 0
                                    else 0.0
                                )
                                buyer_fulfillment[buyer] = min(100.0, fulfillment_pct)
                            else:
                                print(
                                    f"  {buyer} cannot afford external grid for remaining "
                                    f"{remaining_need:.1f} kWh"
                                )
                                print(
                                    f"     (€{self.agent.external_grid_sell_price:.2f}/kWh "
                                    f"> max €{price_max:.2f}/kWh)"
                                )

                        # Sell surplus to external grid
                        for seller, surplus_kwh in surplus_energy.items():
                            total_revenue = (
                                surplus_kwh * self.agent.external_grid_buy_price
                            )

                            print(
                                f"  {seller} selling {surplus_kwh:.1f} kWh to "
                                "external grid "
                                f"@ €{self.agent.external_grid_buy_price:.2f}/kWh"
                            )
                            print(f"     Total revenue: €{total_revenue:.2f}")

                            seller_msg = Message(to=seller)
                            seller_msg.metadata = {
                                "performative": "accept",
                                "type": "offer_accept",
                            }
                            seller_msg.body = json.dumps(
                                {
                                    "round_id": R,
                                    "buyer": "external_grid",
                                    "kw": surplus_kwh,
                                    "price": self.agent.external_grid_buy_price,
                                }
                            )
                            await self.send(seller_msg)

                            self.agent.ext_grid_total_bought_kwh += surplus_kwh
                            self.agent.ext_grid_costs += total_revenue
                            ext_bought_total += surplus_kwh
                            ext_bought_value += total_revenue

                        if ext_sold_total > 0 or ext_bought_total > 0:
                            print("\n[External Grid Summary]")
                            if ext_sold_total > 0:
                                print(
                                    "    Sold to microgrid: "
                                    f"{ext_sold_total:.1f} kWh "
                                    f"@ €{self.agent.external_grid_sell_price:.2f}/kWh "
                                    f"= €{ext_sold_value:.2f}"
                                )
                            if ext_bought_total > 0:
                                print(
                                    "    Bought from microgrid: "
                                    f"{ext_bought_total:.1f} kWh "
                                    f"@ €{self.agent.external_grid_buy_price:.2f}/kWh "
                                    f"= €{ext_bought_value:.2f}"
                                )

                    else:
                        self.agent.ext_grid_rounds_unavailable += 1

                        if len(unmet_demand) > 0 or len(surplus_energy) > 0:
                            print("\nEXTERNAL GRID UNAVAILABLE:\n")

                            if len(unmet_demand) > 0:
                                print("  Unmet demand (potential blackout):")
                                for (
                                    buyer,
                                    _,
                                    remaining,
                                    _,
                                    fulfillment,
                                ) in unmet_demand:
                                    if fulfillment > 0:
                                        print(
                                            f"      {buyer}: {remaining:.1f} kWh not supplied "
                                            f"(only {fulfillment:.0f}% fulfilled)"
                                        )
                                    else:
                                        print(
                                            f"      {buyer}: {remaining:.1f} kWh not supplied"
                                        )

                            if len(surplus_energy) > 0:
                                print("  Wasted surplus (curtailed):")
                                for seller, surplus_kwh in surplus_energy.items():
                                    print(
                                        f"      {seller}: {surplus_kwh:.1f} kWh not sold"
                                    )

                # Collect performance metrics for this round
                round_data = {
                    "total_demand": sum(
                        req_data["need_kwh"] for _, req_data in reqs
                    )
                    if reqs
                    else 0,
                    "total_supplied": total_traded + ext_sold_total,
                    "market_value": total_value + ext_sold_value,
                    "wasted_energy": sum(seller_remaining.values()),
                    "ext_grid_sold": ext_sold_total,
                    "ext_grid_bought": ext_bought_total,
                    "buyer_fulfillment": buyer_fulfillment.copy(),
                    "any_producer_failed": self.agent.any_producer_failed,
                    "emergency_used": self.agent.any_producer_failed,
                    # Monetary values for external grid transactions
                    "ext_grid_sold_value": ext_bought_value,
                    "ext_grid_bought_value": ext_sold_value,
                }

                # Record round (PerformanceTracker may print a report every N rounds)
                self.agent.performance_tracker.record_round(
                    self.agent.round_counter, round_data
                )

                # Log recoveries if any failure counters reached zero
                for p_jid, state in self.agent.producers_state.items():
                    if not state.get("is_operational", True):
                        if state.get("failure_rounds_remaining", 0) == 0:
                            print(f"\n{p_jid} recovered.\n")

                round_sleep = self.agent.config["SIMULATION"]["ROUND_SLEEP_SECONDS"]
                print(
                    f"\nWaiting {round_sleep} seconds before starting the next round..."
                )
                post_env_sleep = round_sleep * 0.2
                pre_env_sleep = max(0.0, round_sleep - post_env_sleep)
                if pre_env_sleep > 0:
                    await asyncio.sleep(pre_env_sleep)

                # Advance simulated time
                self.agent.round_counter += 1

                self.agent.sim_hour += 1
                if self.agent.sim_hour >= 24:
                    self.agent.sim_hour = 0
                    self.agent.sim_day += 1

                # Request next environment update
                update_msg = Message(to=self.agent.env_jid)
                update_msg.metadata = {
                    "performative": "request",
                    "type": "request_environment_update",
                }
                update_msg.body = json.dumps(
                    {"command": "update", "sim_hour": self.agent.sim_hour}
                )
                await self.send(update_msg)

                if post_env_sleep > 0:
                    await asyncio.sleep(post_env_sleep)

    class PrintAgentStatus(OneShotBehaviour):
        """
        Behaviour that prints a snapshot of the current status of
        consumers, prosumers, producers, and storage units.
        """

        async def run(self):
            """
            Print the latest state of all known agents for debugging and
            monitoring purposes.
            """
            print("AGENT STATUS REPORTS:\n")

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
                print(
                    f"  {jid}: Demand = {demand:.1f} kWh | "
                    f"Deficit = {deficit:.1f} kWh"
                )

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

                print(
                    f"  {jid}: Demand = {demand:.1f} kWh | "
                    f"Production = {prod:.1f} kWh | {status} = {net:+.1f} kWh"
                )
                print(
                    f"           Solar: {solar:.2f} | Area: {area:.1f} m² "
                    f"→ {prod:.1f} kWh"
                )

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
                    print(
                        f"  {jid}: Production = {prod:.1f} kWh ({status}) [FAILURE]"
                    )
                else:
                    status = "Available" if prod > 0 else "Offline"

                    if prod_type == "solar":
                        print(
                            f"  {jid}: Production = {prod:.1f} kWh ({status})"
                        )
                        if prod > 0:
                            print(
                                f"           Solar: {solar:.2f} × 20.0 "
                                f"(efficiency × capacity) = {prod:.1f} kWh"
                            )
                    elif prod_type == "wind":
                        if wind > 3.0:
                            if wind < 12.0:
                                power_fraction = (wind - 3.0) / 9.0
                            else:
                                power_fraction = 1.0
                        else:
                            power_fraction = 0.0

                        print(
                            f"  {jid}: Production = {prod:.1f} kWh ({status})"
                        )
                        if prod > 0:
                            print(
                                f"           Wind: {wind:.1f} m/s → "
                                f"{power_fraction:.2f} × 50.0 kWh (capacity) "
                                f"= {prod:.1f} kWh"
                            )
                    else:
                        print(
                            f"  {jid}: Production = {prod:.1f} kWh ({status})"
                        )

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
                        print(
                            f"  {jid}: SOC = {soc:.1f}/{cap:.1f} kWh "
                            f"({pct:.0f}%) | Available: {avail:.1f} kWh "
                            "(emergency mode supplying)"
                        )
                    else:
                        print(
                            f"  {jid}: SOC = {soc:.1f}/{cap:.1f} kWh "
                            f"({pct:.0f}%) | EMERGENCY RESERVE"
                        )
                else:
                    print(
                        f"  {jid}: SOC = {soc:.1f}/{cap:.1f} kWh "
                        f"({pct:.0f}%) | Available: {avail:.1f} kWh"
                    )

            print()

    class PrintTotalsTable(OneShotBehaviour):
        """
        Behaviour that prints an aggregated summary of total demand,
        total available energy, and market balance for the current round.
        """

        def __init__(self, round_id):
            """
            Initialize the PrintTotalsTable behaviour.

            Args:
                round_id (float): Identifier of the round for which this
                    summary is being printed.
            """
            super().__init__()
            self.round_id = round_id

        async def run(self):
            """
            Compute and print the total demand, total available energy,
            number of buyers and sellers, and market balance.
            """
            total_demand = 0.0
            total_available = 0.0
            num_buyers = 0
            num_sellers = 0

            # Households
            for state in self.agent.households_state.values():
                demand = state.get("demand_kwh", 0)
                prod = state.get("prod_kwh", 0)
                if demand > prod:
                    total_demand += (demand - prod)
                    num_buyers += 1
                elif prod > demand:
                    total_available += (prod - demand)
                    num_sellers += 1

            # Producers
            for state in self.agent.producers_state.values():
                prod = state.get("prod_kwh", 0)
                if prod > 0 and state.get("is_operational", True):
                    total_available += prod
                    num_sellers += 1

            # Storage
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
            print(
                "║"
                + " " * 10
                + "GRID ENERGY MARKET - ROUND SUMMARY"
                + " " * 14
                + "║"
            )
            print("╠" + "=" * 58 + "╣")
            print(
                f"║  Total Demand:     {total_demand:7.1f} kWh  ({num_buyers} buyers)"
                + " " * (58 - 44 - len(str(num_buyers)))
                + "║"
            )
            print(
                f"║  Total Available:  {total_available:7.1f} kWh  ({num_sellers} sellers)"
                + " " * (58 - 46 - len(str(num_sellers)))
                + "║"
            )
            print(
                f"║  Market Balance:   {balance:+7.1f} kWh ({status})"
                + " " * (58 - 37 - len(status))
                + "║"
            )
            print("╚" + "=" * 58 + "╝\n")

    class _InviteBurstSend(OneShotBehaviour):
        """
        Behaviour that sends a "call for offers" (CFP) message burst to
        all eligible agents for the current round.
        """

        def __init__(self, round_id, seller_jids, deadline_ts, producers_failed=False):
            """
            Initialize the _InviteBurstSend behaviour.

            Args:
                round_id (float): Identifier of the round.
                seller_jids (list[str]): List of agent JIDs to invite.
                deadline_ts (float): UNIX timestamp representing the
                    deadline for sending offers.
                producers_failed (bool): Indicates whether any producer
                    is currently in a failure state (used as contextual info).
            """
            super().__init__()
            self.round_id = round_id
            self.seller_jids = seller_jids
            self.deadline_ts = deadline_ts
            self.producers_failed = producers_failed

        async def run(self):
            """
            Send CFP messages to all target agents, including round id,
            deadline, and whether producers have failed.
            """
            for jid in self.seller_jids:
                msg = Message(to=jid)
                msg.metadata = {"performative": "cfp", "type": "call_for_offers"}
                msg.body = json.dumps(
                    {
                        "round_id": self.round_id,
                        "deadline_ts": self.deadline_ts,
                        "producers_failed": self.producers_failed,
                    }
                )
                await self.send(msg)
