from spade.behaviour import OneShotBehaviour
from spade.message import Message
import time
import json
import random
import asyncio
from agents.grid_node.print_status import PrintAgentStatus
from agents.grid_node.print_totals import PrintTotalsTable
from agents.grid_node.invite_burst import InviteBurstSend

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
        def limit_suffix(limit_info, deliverable_value=None):
            """
            Compose the string that lists an agent's limit and deliverable capacity.
            """
            if not limit_info:
                limit_info = {}

            label = limit_info.get("display")
            effective_limit = limit_info.get("effective_limit")

            parts = []
            if label:
                parts.append(label)
            elif effective_limit is not None:
                parts.append(f"limit {effective_limit:.1f} kWh")

            if deliverable_value is not None:
                parts.append(f"deliverable {deliverable_value:.1f} kWh")

            if not parts:
                return ""

            return " | " + " | ".join(parts)

        def format_need_line(agent_label, needs_value, limit_info, deliverable_value):
            """
            Build the textual description for a buyer's demand line.
            """
            return (
                f"{agent_label} needs {needs_value:.1f} kWh"
                f"{limit_suffix(limit_info, deliverable_value)}"
            )
        while True:
            R = time.time()
            self.agent.round_id = R
            self.agent.round_start_ts = R

            elapsed_real = R - self.agent.simulation_start_ts
            demand_period = self.agent._get_demand_period(self.agent.sim_hour)
            period_emoji = self._get_period_emoji(self.agent.sim_hour)

            print("\n" + "=" * 80)
            print(f"  ROUND #{self.agent.round_counter}")
            print(
                f"  Simulated Time: Day {self.agent.sim_day} - "
                f"{self.agent.sim_hour:02d}:00 ({period_emoji} {demand_period})"
            )
            print(f"  Real Time Elapsed: {elapsed_real:.1f}s")
            print("=" * 80)
            print(
                "🌏 Environment: "
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
            print_status = PrintAgentStatus()
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
            print_table = PrintTotalsTable(R)
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
                print("⚙️  AUCTION PROCESS:\n")
                print("➡️  Broadcasting Call for Proposals to eligible agents...")
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
                burst = InviteBurstSend(
                    R,
                    list(eligible_for_cfp),
                    self.agent.round_deadline_ts,
                    self.agent.any_producer_failed,
                )
                self.agent.add_behaviour(burst)
                await asyncio.sleep(offers_timeout)
            else:
                print("⚙️ No agents available for auction.\n")

            # Collect offers and requests for this round
            offers = self.agent.offers_round.get(R, {})
            reqs = list(self.agent.requests_round.get(R, {}).items())
            req_lookup = dict(reqs)
            declined = self.agent.declined_round.get(R, set())

            seller_limit_info = {}
            seller_initial_deliverable = {}
            for seller, offer_data in offers.items():
                limit_info = self.agent.get_operational_limit_info(seller, "sell")
                seller_limit_info[seller] = limit_info
                limit_value = limit_info.get("effective_limit")
                offer_amount = offer_data["offer_kwh"]
                deliverable_offer = offer_amount
                if limit_value is not None:
                    deliverable_offer = min(deliverable_offer, limit_value)
                deliverable_offer = max(0.0, deliverable_offer)
                seller_initial_deliverable[seller] = deliverable_offer

            print(f"📩 OFFERS RECEIVED ({len(offers)} of {len(sellers)} invited):")
            for seller, offer_data in offers.items():
                kwh = offer_data["offer_kwh"]
                price = offer_data["price"]
                deliverable_offer = seller_initial_deliverable.get(seller, kwh)
                limit_note = limit_suffix(
                    seller_limit_info.get(seller), deliverable_offer
                )
                print(
                    f"  {seller}: {kwh:.1f} kWh @ €{price:.2f}/kWh{limit_note}"
                )

            if len(declined) > 0:
                print(f"\n📭 NO RESPONSE ({len(declined)}):")
                for agent_jid in declined:
                    print(f"  {agent_jid} (declined to participate)")

            print("\n🤝 MATCHING:\n")

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
            for seller, deliverable in seller_initial_deliverable.items():
                seller_remaining[seller] = deliverable

            buyer_caps = {}

            for buyer, req_data in reqs:
                need_kwh = req_data["need_kwh"]
                price_max = req_data["price_max"]
                limit_info = self.agent.get_operational_limit_info(buyer, "buy")
                limit_value = limit_info.get("effective_limit")
                deliverable_cap = need_kwh
                if limit_value is not None:
                    deliverable_cap = min(deliverable_cap, limit_value)
                deliverable_cap = max(0.0, deliverable_cap)
                buyer_caps[buyer] = {
                    "limit_info": limit_info,
                    "deliverable_cap": deliverable_cap,
                }

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
                    print(
                        "  ⚠️  "
                        + format_need_line(
                            buyer, need_kwh, limit_info, deliverable_cap
                        )
                    )
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
                    remaining_deliverable = max(0.0, deliverable_cap - total_bought)
                    transmission_remaining = max(
                        0.0, self.agent.transmission_limit_kw - total_bought
                    )

                    if remaining_deliverable <= 0 or transmission_remaining <= 0:
                        break

                    raw_allocation = min(available, remaining_deliverable)
                    if raw_allocation <= 0:
                        continue

                    amount = min(raw_allocation, transmission_remaining)
                    if amount <= 0:
                        break

                    if amount < raw_allocation:
                        log_msg = (
                            "⚠️ [TRANSMISSION LIMIT] Original offer of "
                            f"{raw_allocation:.1f} kWh limited to "
                            f"{amount:.1f} kWh."
                        )
                        print(f"        {log_msg}")
                        self.agent._add_event(
                            "transmission_limit",
                            buyer,
                            {
                                "seller": seller,
                                "original_kwh": raw_allocation,
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

                demand_line = format_need_line(
                    buyer, need_kwh, limit_info, deliverable_cap
                )

                if total_bought > 0:
                    fulfillment_pct = (total_bought / need_kwh) * 100
                    buyer_received_kw[buyer] = total_bought
                    buyer_fulfillment[buyer] = fulfillment_pct

                    if fulfillment_pct >= 99.0:
                        print(f"  ✅ {demand_line}")
                        matched_count += 1
                    else:
                        print(f"  ⚠️ {demand_line}")
                        partial_count += 1

                    for _, (seller, amount, price, cost) in enumerate(purchases):
                        remaining_after = seller_remaining[seller]
                        seller_before = remaining_after + amount

                        print(
                            f"     • Matched with {seller} @ €{price:.2f}/kWh "
                            f"({amount:.1f} kWh, €{cost:.2f})"
                        )
                        print(
                            f"        {seller} remaining: "
                            f"{remaining_after:.1f} kWh "
                            f"(was {seller_before:.1f} kWh)"
                        )

                    avg_price = total_cost / total_bought if total_bought > 0 else 0
                    print(
                        f"     • {buyer} received {total_bought:.1f}/"
                        f"{need_kwh:.1f} kWh ({fulfillment_pct:.0f}% fulfilled)"
                    )
                    print(
                        f"     • Total cost: €{total_cost:.2f} "
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
                    print(f"  ⚠️ {demand_line}")
                    print("     • No match\n")
                    unmatched_count += 1
                    buyer_fulfillment[buyer] = 0.0

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
                        cap_info = buyer_caps.get(buyer, {})
                        unmet_demand.append(
                            (
                                buyer,
                                need_kwh,
                                remaining,
                                price_max,
                                fulfillment,
                                cap_info,
                            )
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
                        print("\n🌐 EXTERNAL GRID AVAILABLE:")
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
                        cap_info,
                    ) in unmet_demand:
                        if self.agent.external_grid_sell_price <= price_max:
                            current_received = buyer_received_kw.get(buyer, 0.0)
                            deliverable_cap = cap_info.get("deliverable_cap", need_kwh)
                            limit_info = cap_info.get("limit_info")
                            agent_remaining = max(
                                0.0, deliverable_cap - current_received
                            )
                            transmission_remaining = max(
                                0.0,
                                self.agent.transmission_limit_kw - current_received,
                            )

                            if agent_remaining <= 0:
                                limit_note = limit_suffix(
                                    limit_info, agent_remaining
                                )
                                print(
                                    f"  {buyer} already at deliverable cap"
                                    f"{limit_note}. Skipping external supply."
                                )
                                continue

                            if transmission_remaining <= 0:
                                print(
                                    f"  {buyer} already at transmission limit "
                                    f"({self.agent.transmission_limit_kw:.1f} kWh). "
                                    "Skipping external supply."
                                )
                                continue

                            allowed_cap = min(agent_remaining, transmission_remaining)
                            delivered = min(remaining_need, allowed_cap)
                            if delivered <= 0:
                                continue

                            total_cost = (
                                delivered * self.agent.external_grid_sell_price
                            )

                            if current_fulfillment > 0:
                                print(
                                    f"  🌐 {buyer} buying additional "
                                    f"{delivered:.1f} kWh from external grid "
                                    f"@ €{self.agent.external_grid_sell_price:.2f}/kWh"
                                )
                            else:
                                print(
                                    f"  🌐 {buyer} buying {delivered:.1f} kWh from "
                                    "external grid "
                                    f"@ €{self.agent.external_grid_sell_price:.2f}/kWh"
                                )

                            if delivered < remaining_need:
                                reasons = []
                                if agent_remaining < remaining_need:
                                    reasons.append("agent deliverable cap")
                                if transmission_remaining < remaining_need:
                                    reasons.append("transmission limit")
                                reason_text = " & ".join(reasons) or "capacity cap"
                                log_msg = (
                                    f"[{reason_text.upper()}] Original demand of "
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
                                        "reasons": reasons,
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
                            print(
                                f"     Final fulfillment: {buyer_fulfillment[buyer]:.0f}%"
                            )
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
                            f"  🌐 {seller} selling {surplus_kwh:.1f} kWh to "
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
                        print("\n🌐 [External Grid Summary]")
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
                        pass

            blackout_impacted = sum(
                1 for pct in buyer_fulfillment.values() if pct < 99.0
            )
            avg_fulfillment = (
                sum(buyer_fulfillment.values()) / len(buyer_fulfillment)
                if buyer_fulfillment
                else 0.0
            )
            blackout_round = blackout_impacted > 0

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
                "avg_fulfillment": avg_fulfillment,
                "blackout": blackout_round,
                "blackout_impacted": blackout_impacted,
            }

            round_sleep = self.agent.config["SIMULATION"]["ROUND_SLEEP_SECONDS"]
            post_env_sleep = round_sleep * 0.2
            pre_env_sleep = max(0.0, round_sleep - post_env_sleep)

            blackout_details = {
                agent: pct
                for agent, pct in buyer_fulfillment.items()
                if pct < 99.0
            }
            if blackout_details:
                print("\n🚨 Blackout impact:")
                for agent, pct in sorted(blackout_details.items()):
                    print(f"   {agent}: {pct:.0f}% fulfilled")
            else:
                print("\n✅ No blackout impact this round.")

            self._print_auction_results_summary(
                total_buyers=len(reqs),
                matched_count=matched_count,
                partial_count=partial_count,
                unmatched_count=unmatched_count,
                declined_count=len(declined),
                total_traded=total_traded,
                total_value=total_value,
                prices_paid=prices_paid,
                blackout_happened=blackout_round,
                blackout_impacted=blackout_impacted,
                round_sleep=round_sleep,
            )

            # Record round (PerformanceTracker may print a report every N rounds)
            self.agent.performance_tracker.record_round(
                self.agent.round_counter, round_data
            )

            # Log recoveries if any failure counters reached zero
            for p_jid, state in self.agent.producers_state.items():
                if not state.get("is_operational", True):
                    if state.get("failure_rounds_remaining", 0) == 0:
                        print(f"\n✅ {p_jid} recovered.\n")

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

    def _print_auction_results_summary(
        self,
        total_buyers,
        matched_count,
        partial_count,
        unmatched_count,
        declined_count,
        total_traded,
        total_value,
        prices_paid,
        blackout_happened,
        blackout_impacted,
        round_sleep,
    ):
        """
        Print a concise auction summary at the end of each round.
        """
        print("\n📊 AUCTION RESULTS:")
        print(f"   🛒 Buyers requesting energy: {total_buyers}")
        if matched_count > 0:
            print(f"   ✅ Fully matched: {matched_count}")
        if partial_count > 0:
            print(f"   ⚠️ Partial matches: {partial_count}")
        if unmatched_count > 0:
            print(f"   🚫 Unmatched requests: {unmatched_count}")
        if declined_count > 0:
            print(f"   🙅 Sellers declined: {declined_count}")
        if total_traded > 0:
            print(f"   🔄 Energy traded: {total_traded:.1f} kWh")
            print(f"   💰 Market value: €{total_value:.2f}")
            avg_price = (
                sum(prices_paid) / len(prices_paid) if prices_paid else 0
            )
            print(f"   📈 Avg price: €{avg_price:.2f}/kWh")
        if blackout_happened:
            print(f"   🚨 Blackout: YES ({blackout_impacted} agent(s) affected)")
        else:
            print("   ✅ Blackout: NO")

    def _format_energy_state(self, agent_jid):
        """
        Return a string with extra energy state info for unmet demand logs.
        """
        state = self.agent.storage_state.get(agent_jid)
        if state:
            soc = state.get("soc_kwh", 0.0)
            cap = state.get("cap_kwh", 0.0)
            pct = (soc / cap * 100) if cap > 0 else 0.0
            return f" | SOC {pct:.0f}%"

        household_state = self.agent.households_state.get(agent_jid)
        if household_state and household_state.get("is_prosumer", False):
            battery_kwh = household_state.get("battery_kwh", 0.0)
            cap = self.agent.config["HOUSEHOLDS"]["BATTERY_CAPACITY_KWH"]
            pct = (battery_kwh / cap * 100) if cap > 0 else 0.0
            pct = max(0.0, min(100.0, pct))
            return f" | Battery {pct:.0f}%"

        return ""

    def _get_period_emoji(self, hour):
        """
        Map the current hour to an emoji representing the demand period.
        """
        if 6 <= hour < 9:
            return "🌅"
        if 9 <= hour < 18:
            return "☀️"
        if 18 <= hour < 22:
            return "🌆"
        return "🌙"
