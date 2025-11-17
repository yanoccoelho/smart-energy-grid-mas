import time
import json
import random
import spade
from spade.behaviour import CyclicBehaviour, OneShotBehaviour
from spade.message import Message
from logs.db_logger import DBLogger
from scenarios.base_config import SCENARIO_CONFIG


class ProducerAgent(spade.agent.Agent):
    """
    ProducerAgent represents a solar or wind power producer in the microgrid.
    It generates energy based on environmental conditions and supports
    random failure simulation (with automatic recovery).

    Responsibilities:
        - Register itself with the GridNode.
        - Receive environment updates and compute current production.
        - Simulate temporary failures (producer offline for N rounds).
        - Respond to CFPs (Call for Proposals) with energy offers.
        - Report production values to the GridNode each round.

    Args:
        jid (str): XMPP address of the agent.
        password (str): Authentication password.
        grid_node_jid (str): JID of the GridNode agent.
        production_type (str): "solar" or "wind".
        max_capacity_kw (float): Maximum production capacity (kWh).
        ask_price (float): Base energy price when offering.
        response_probability (float): Chance of responding to a CFP.
        config (dict): Global scenario configuration.

    Attributes:
        is_operational (bool): Whether producer is online.
        failure_rounds_remaining (int): Remaining rounds until recovery.
        failure_rounds_total (int): Full number of rounds it stays offline.
        current_production_kwh (float): Latest computed production.
        solar_irradiance (float): Latest irradiance value.
        wind_speed (float): Latest wind speed.
        temperature (float): Latest temperature.
        active_round_id (int | None): Current CFP round.
        round_deadline_ts (float): Deadline timestamp for sending proposal.
    """

    def __init__(
        self,
        jid,
        password,
        grid_node_jid,
        production_type="solar",
        max_capacity_kw=100.0,
        ask_price=0.18,
        response_probability=0.85,
        config=SCENARIO_CONFIG
    ):
        super().__init__(jid, password)
        self.grid_node_jid = grid_node_jid
        self.production_type = production_type
        self.max_capacity_kwh = max_capacity_kw
        self.current_production_kwh = 0.0
        self.ask_price = float(ask_price)
        self.response_probability = float(response_probability)
        self.config = config

        # Failure simulation
        self.is_operational = True
        self.failure_rounds_remaining = 0
        self.failure_rounds_total = 0
        self.last_decrement_round = None

        # Auction state
        self.active_round_id = None
        self.round_deadline_ts = 0.0

        # Environment
        self.solar_irradiance = 0.0
        self.wind_speed = 0.0
        self.temperature = 20.0

        # Logging
        self.db_logger = DBLogger()

    async def setup(self):
        """
        Setup lifecycle method.

        Behaviours added:
            - InitialSetup(): registers producer and sends initial status.
            - RoundReceiver(): waits for environment updates and CFPs.
        """
        self.add_behaviour(self.InitialSetup())
        self.add_behaviour(self.RoundReceiver())

    def _update_production(self):
        """
        Computes the current energy production based on:
            - Solar irradiance (for solar producers)
            - Wind speed (for wind producers)
            - Noise factor (random variability)
            - Failure state (zero production when offline)
        """
        if not self.is_operational:
            self.current_production_kwh = 0.0
            return

        noise_min, noise_max = self.config["PRODUCERS"]["PRODUCTION_NOISE_RANGE"]
        noise_factor = random.uniform(noise_min, noise_max)

        # Solar production model
        if self.production_type == "solar":
            if self.solar_irradiance > 0:
                efficiency = self.config["PRODUCERS"]["SOLAR_EFFICIENCY"]
                prod_kwh = (
                    self.solar_irradiance
                    * efficiency
                    * self.max_capacity_kwh
                    * noise_factor
                )
                self.current_production_kwh = min(prod_kwh, self.max_capacity_kwh)
            else:
                self.current_production_kwh = 0.0

        # Wind production model
        elif self.production_type == "wind":
            if self.wind_speed > 3.0:
                # Power curve approximation
                if self.wind_speed < 12.0:
                    power_fraction = (self.wind_speed - 3.0) / 9.0
                else:
                    power_fraction = 1.0
                capacity_factor = self.config["PRODUCERS"]["WIND_CAPACITY_FACTOR"]
                prod_kwh = (
                    power_fraction
                    * capacity_factor
                    * self.max_capacity_kwh
                    * noise_factor
                )
                self.current_production_kwh = min(prod_kwh, self.max_capacity_kwh)
            else:
                self.current_production_kwh = 0.0

    class InitialSetup(OneShotBehaviour):
        """
        Behaviour executed once at startup:
            - Registers the producer in the GridNode.
            - Computes and reports initial production.
        """

        async def run(self):
            # Register producer
            register_msg = Message(to=self.agent.grid_node_jid)
            register_msg.metadata = {
                "performative": "inform",
                "type": "register_producer"
            }
            register_msg.body = json.dumps({
                "jid": str(self.agent.jid),
                "production_type": self.agent.production_type,
                "max_capacity_kwh": self.agent.max_capacity_kwh,
                "timestamp": time.time()
            })
            await self.send(register_msg)

            # Compute first production
            self.agent._update_production()

            # Send initial production report
            prod_msg = Message(to=self.agent.grid_node_jid)
            prod_msg.metadata = {"performative": "inform", "type": "production_report"}
            prod_msg.body = json.dumps({
                "jid": str(self.agent.jid),
                "prod_kwh": self.agent.current_production_kwh,
                "type": self.agent.production_type,
                "is_operational": self.agent.is_operational,
                "failure_rounds_remaining": self.agent.failure_rounds_remaining,
                "failure_rounds_total": self.agent.failure_rounds_total,
                "solar_irradiance": self.agent.solar_irradiance,
                "wind_speed": self.agent.wind_speed,
                "temperature_c": self.agent.temperature,
                "timestamp": time.time()
            })
            await self.send(prod_msg)

    class RoundReceiver(CyclicBehaviour):
        """
        Behaviour that continuously receives:
            - environment_update: recompute production and report it.
            - call_for_offers: participate in market auction.
            - offer_accept: reserved for future logic.
        """

        async def run(self):
            msg = await self.receive(timeout=0.5)
            if not msg:
                return

            msg_type = msg.metadata.get("type", "")

            # ENVIRONMENT UPDATE
            if msg_type == "environment_update":
                data = json.loads(msg.body)
                self.agent.solar_irradiance = data.get("solar_irradiance", 0)
                self.agent.wind_speed = data.get("wind_speed", 0)
                self.agent.temperature = data.get("temperature_c", 20)

                # Failure recovery logic
                if not self.agent.is_operational:
                    current_round = self.agent.active_round_id
                    if (
                        current_round
                        and current_round != self.agent.last_decrement_round
                    ):
                        if self.agent.failure_rounds_remaining > 0:
                            self.agent.failure_rounds_remaining -= 1
                            self.agent.last_decrement_round = current_round

                            # Auto-recovery
                            if self.agent.failure_rounds_remaining == 0:
                                self.agent.is_operational = True
                                print(f"[RECOVERY] {self.agent.jid} has recovered.")

                # Compute new production
                self.agent._update_production()

                # Report production
                prod_msg = Message(to=self.agent.grid_node_jid)
                prod_msg.metadata = {
                    "performative": "inform",
                    "type": "production_report"
                }
                prod_msg.body = json.dumps({
                    "jid": str(self.agent.jid),
                    "prod_kwh": self.agent.current_production_kwh,
                    "type": self.agent.production_type,
                    "is_operational": self.agent.is_operational,
                    "failure_rounds_remaining": self.agent.failure_rounds_remaining,
                    "failure_rounds_total": self.agent.failure_rounds_total,
                    "solar_irradiance": self.agent.solar_irradiance,
                    "wind_speed": self.agent.wind_speed,
                    "temperature_c": self.agent.temperature,
                    "timestamp": time.time()
                })
                await self.send(prod_msg)

            # MARKET CFP
            elif msg_type == "call_for_offers":
                data = json.loads(msg.body)
                self.agent.active_round_id = data.get("round_id")
                self.agent.round_deadline_ts = data.get("deadline_ts", 0)
                self.agent.add_behaviour(self.agent.OfferBehaviour())

            # OFFER ACCEPTED (future logic)
            elif msg_type == "offer_accept":
                pass

    class OfferBehaviour(OneShotBehaviour):
        """
        Behaviour executed when a CFP is received. The producer:
            - Checks operational state.
            - Decides (probabilistically) whether to respond.
            - Sends an energy offer with slight price variation.
        """

        async def run(self):
            R = self.agent.active_round_id

            # Basic filters
            if (
                not R
                or not self.agent.is_operational
                or self.agent.current_production_kwh <= 0.01
            ):
                return

            # Random refusal (autonomy)
            if random.random() > self.agent.response_probability:
                decline_msg = Message(to=self.agent.grid_node_jid)
                decline_msg.metadata = {
                    "performative": "refuse",
                    "type": "declined_offer"
                }
                decline_msg.body = json.dumps({
                    "round_id": R,
                    "reason": "agent_decision"
                })
                await self.send(decline_msg)
                return

            # Check deadline and send offer
            now = time.time()
            if now <= self.agent.round_deadline_ts:
                base_price = self.agent.ask_price
                price_variation = random.uniform(-0.02, 0.02)
                final_price = base_price * (1 + price_variation)

                msg = Message(to=self.agent.grid_node_jid)
                msg.metadata = {"performative": "propose", "type": "energy_offer"}
                msg.body = json.dumps({
                    "round_id": R,
                    "offer_kwh": self.agent.current_production_kwh,
                    "price": round(final_price, 2)
                })
                await self.send(msg)
