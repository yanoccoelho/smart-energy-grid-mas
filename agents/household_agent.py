import time
import json
import random
import spade
from spade.behaviour import CyclicBehaviour, OneShotBehaviour
from spade.message import Message
from logs.db_logger import DBLogger
from scenarios.base_config import SCENARIO_CONFIG


class HouseholdAgent(spade.agent.Agent):
    """
    HouseholdAgent represents either a consumer or a prosumer in the microgrid.

    This agent:
        - Computes its own energy demand and production.
        - Manages a local battery (if prosumer).
        - Responds to environment updates.
        - Participates in auction rounds by sending offers or requests.

    Args:
        jid (str): XMPP address of the agent.
        password (str): Authentication password.
        grid_node_jid (str): JID of the GridNode agent.
        is_prosumer (bool): Whether the agent produces energy.
        price_max (float): Maximum price it is willing to pay when buying energy.
        ask_price (float): Base price when selling energy.
        response_probability (float): Probability of responding to a CFP as a seller.
        config (dict): Scenario configuration dictionary.

    Attributes:
        current_demand_kwh (float): Latest computed energy demand.
        current_production_kwh (float): Latest computed energy production.
        battery_kwh (float): Current battery storage level.
        panel_area_m2 (float): PV panel area (if prosumer).
        active_round_id (float | None): Current auction round ID.
        round_deadline_ts (float): CFP deadline timestamp.
    """

    def __init__(
        self,
        jid,
        password,
        grid_node_jid,
        is_prosumer=False,
        price_max=0.25,
        ask_price=0.20,
        response_probability=0.85,
        config=SCENARIO_CONFIG
    ):
        super().__init__(jid, password)
        self.grid_node_jid = grid_node_jid
        self.is_prosumer = is_prosumer
        self.config = config
        self.price_max = float(price_max)
        self.ask_price = float(ask_price)
        self.response_probability = float(response_probability)

        # Energy state
        self.current_demand_kwh = 0.0
        self.current_production_kwh = 0.0
        self.battery_kwh = 0.0

        # Battery (only for prosumers)
        self.battery_capacity_kwh = (
            self.config["HOUSEHOLDS"]["BATTERY_CAPACITY_KWH"] if is_prosumer else 0.0
        )
        self.max_charge_kwh = self.config["HOUSEHOLDS"]["BATTERY_CHARGE_RATE_KW"]
        self.max_discharge_kwh = self.config["HOUSEHOLDS"]["BATTERY_DISCHARGE_RATE_KW"]
        self.battery_efficiency = self.config["HOUSEHOLDS"]["BATTERY_EFFICIENCY"]

        # PV area
        if is_prosumer:
            min_area, max_area = self.config["HOUSEHOLDS"]["PANEL_AREA_RANGE_M2"]
            self.panel_area_m2 = random.uniform(min_area, max_area)
        else:
            self.panel_area_m2 = 0.0

        # Environment
        self.solar_irradiance = 0.0
        self.wind_speed = self.config["ENVIRONMENT"]["BASE_WIND_SPEED"]
        self.temperature = self.config["ENVIRONMENT"]["BASE_TEMPERATURE"]
        self.sim_hour = 6

        # Auction state
        self.active_round_id = None
        self.round_deadline_ts = 0.0

        self.db_logger = DBLogger()

    async def setup(self):
        """
        Setup lifecycle method.

        Responsibilities:
            - Registers the agent in the GridNode.
            - Starts listening for environment and auction messages.
        """
        self.add_behaviour(self.InitialSetup())
        self.add_behaviour(self.RoundReceiver())

    def _update_state(self):
        """
        Compute the current energy state:
            - Demand (varies by time of day)
            - Production (if prosumer)
            - Battery charging/discharging
        """
        hour = self.sim_hour
        demand_ranges = self.config["HOUSEHOLDS"]["DEMAND_RANGES"]

        # Demand model based on time-of-day
        if 6 <= hour < 9:
            demand_range = demand_ranges["morning"]
        elif 18 <= hour < 24:
            demand_range = demand_ranges["evening"]
        elif 0 <= hour < 6:
            demand_range = demand_ranges["night"]
        else:
            demand_range = demand_ranges["afternoon"]

        base_demand = random.uniform(*demand_range)
        self.current_demand_kwh = base_demand + random.uniform(-0.3, 0.3)

        # Production (only prosumers with sun)
        if self.is_prosumer and self.solar_irradiance > 0:
            solar_efficiency = self.config["PRODUCERS"]["SOLAR_EFFICIENCY"]
            max_power_kwh = self.panel_area_m2 * solar_efficiency
            self.current_production_kwh = self.solar_irradiance * max_power_kwh
        else:
            self.current_production_kwh = 0.0

        # Battery behavior
        net = self.current_production_kwh - self.current_demand_kwh

        if net > 0 and self.battery_kwh < self.battery_capacity_kwh:
            # Charging
            charge = min(net, self.max_charge_kwh, self.battery_capacity_kwh - self.battery_kwh)
            self.battery_kwh += charge * self.battery_efficiency
            self.battery_kwh = min(self.battery_kwh, self.battery_capacity_kwh)

        elif net < 0 and self.battery_kwh > 0:
            # Discharging
            available = self.battery_kwh * self.battery_efficiency
            discharge = min(-net, self.max_discharge_kwh, available)
            consumed = discharge / self.battery_efficiency if self.battery_efficiency > 0 else discharge
            self.battery_kwh = max(0.0, self.battery_kwh - consumed)

    class InitialSetup(OneShotBehaviour):
        """
        Behaviour executed once when the agent starts.

        Sends:
            - register_household
            - initial status_report
        """

        async def run(self):
            register_msg = Message(to=self.agent.grid_node_jid)
            register_msg.metadata = {"performative": "inform", "type": "register_household"}
            register_msg.body = json.dumps({
                "jid": str(self.agent.jid),
                "is_prosumer": self.agent.is_prosumer,
                "timestamp": time.time()
            })
            await self.send(register_msg)

            # Initial computation
            self.agent._update_state()

            # First status
            status_msg = Message(to=self.agent.grid_node_jid)
            status_msg.metadata = {"performative": "inform", "type": "status_report"}
            status_msg.body = json.dumps({
                "jid": str(self.agent.jid),
                "is_prosumer": self.agent.is_prosumer,
                "demand_kwh": self.agent.current_demand_kwh,
                "prod_kwh": self.agent.current_production_kwh,
                "battery_kwh": self.agent.battery_kwh,
                "panel_area_m2": self.agent.panel_area_m2,
                "solar_irradiance": self.agent.solar_irradiance,
                "wind_speed": self.agent.wind_speed,
                "temperature_c": self.agent.temperature,
                "timestamp": time.time()
            })
            await self.send(status_msg)

    class RoundReceiver(CyclicBehaviour):
        """
        Behaviour that listens for:
            - environment_update
            - call_for_offers
            - auction confirmations
        """

        async def run(self):
            msg = await self.receive(timeout=0.5)
            if not msg:
                return

            msg_type = msg.metadata.get("type", "")

            # Environment updated
            if msg_type == "environment_update":
                data = json.loads(msg.body)
                self.agent.solar_irradiance = data.get("solar_irradiance", 0)
                self.agent.wind_speed = data.get("wind_speed", 0)
                self.agent.temperature = data.get("temperature_c", 20)
                self.agent.sim_hour = data.get("sim_hour", 6)

                # Recompute energy state
                self.agent._update_state()

                status_msg = Message(to=self.agent.grid_node_jid)
                status_msg.metadata = {"performative": "inform", "type": "status_report"}
                status_msg.body = json.dumps({
                    "jid": str(self.agent.jid),
                    "is_prosumer": self.agent.is_prosumer,
                    "demand_kwh": self.agent.current_demand_kwh,
                    "prod_kwh": self.agent.current_production_kwh,
                    "battery_kwh": self.agent.battery_kwh,
                    "panel_area_m2": self.agent.panel_area_m2,
                    "solar_irradiance": self.agent.solar_irradiance,
                    "wind_speed": self.agent.wind_speed,
                    "temperature_c": self.agent.temperature,
                    "timestamp": time.time()
                })
                await self.send(status_msg)

            # CFP received → prepare to bid
            elif msg_type == "call_for_offers":
                data = json.loads(msg.body)
                self.agent.active_round_id = data.get("round_id")
                self.agent.round_deadline_ts = data.get("deadline_ts", 0)
                self.agent.add_behaviour(self.agent.QuickBid())

            # Trade confirmations (not used but kept for future logic)
            elif msg_type in ["control_command", "offer_accept"]:
                pass

    class QuickBid(OneShotBehaviour):
        """
        Behaviour executed when a CFP is received.
        Decides whether to:
            - Request energy (buyer)
            - Offer surplus energy (seller)
        """

        async def run(self):
            R = self.agent.active_round_id
            if not R:
                return

            net = self.agent.current_production_kwh - self.agent.current_demand_kwh

            # BUYER (needs energy)
            if net < -0.1:
                base_price_max = self.agent.price_max
                price_variation = random.uniform(-0.02, 0.02)
                final_price_max = base_price_max * (1 + price_variation)

                msg = Message(to=self.agent.grid_node_jid)
                msg.metadata = {"performative": "request", "type": "energy_request"}
                msg.body = json.dumps({
                    "round_id": R,
                    "need_kwh": abs(net),
                    "price_max": round(final_price_max, 2)
                })
                await self.send(msg)

            # SELLER (has surplus)
            elif net > 0.1 and self.agent.is_prosumer:

                # Random refusal (simulates agent autonomy)
                if random.random() > self.agent.response_probability:
                    decline_msg = Message(to=self.agent.grid_node_jid)
                    decline_msg.metadata = {"performative": "refuse", "type": "declined_offer"}
                    decline_msg.body = json.dumps({
                        "round_id": R,
                        "reason": "agent_decision"
                    })
                    await self.send(decline_msg)
                    return

                # Check deadline
                now = time.time()
                if now <= self.agent.round_deadline_ts:
                    base_price = self.agent.ask_price
                    price_variation = random.uniform(-0.02, 0.02)
                    final_price = base_price * (1 + price_variation)

                    msg = Message(to=self.agent.grid_node_jid)
                    msg.metadata = {"performative": "propose", "type": "energy_offer"}
                    msg.body = json.dumps({
                        "round_id": R,
                        "offer_kwh": net,
                        "price": round(final_price, 2)
                    })
                    await self.send(msg)
