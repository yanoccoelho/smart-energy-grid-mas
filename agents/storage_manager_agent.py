import time
import json
import random
import spade
import asyncio
from spade.behaviour import PeriodicBehaviour, OneShotBehaviour, CyclicBehaviour
from spade.message import Message
from logs.db_logger import DBLogger
from scenarios.base_config import SCENARIO_CONFIG


class StorageManagerAgent(spade.agent.Agent):
    """
    StorageManagerAgent represents a centralized battery storage system used as
    emergency reserve in the microgrid. It supports:
        - Registration in the GridNode.
        - Periodic status reporting (SOC, SOH, temperature).
        - Emergency-only operation mode.
        - Automatic decisions to request or offer energy.
        - Minimum energy reserve preservation (20% SOC).
        - Participation in CFP (Call for Proposals) auction rounds.

    Args:
        jid (str): XMPP address of the agent.
        password (str): Authentication password.
        grid_node_jid (str): JID of the GridNode agent.
        soc_init_frac (float): Initial SOC fraction (0–1).
        config (dict): Global configuration.

    Attributes:
        cap_kwh (float): Total battery capacity.
        soc_kwh (float): Current State of Charge.
        temp_c (float): Temperature in °C.
        soh (float): State of Health (1.0 = perfect).
        ask_price (float): Base selling price.
        price_max (float): Max buying price.
        emergency_only (bool): If true, only operates after producer failures.
        active_round_id (int | None): ID of current auction round.
        round_deadline_ts (float): Deadline timestamp to respond to CFP.
    """

    def __init__(
        self,
        jid,
        password,
        grid_node_jid,
        soc_init_frac=1.0,
        config=SCENARIO_CONFIG,
    ):
        super().__init__(jid, password)

        self.grid_node_jid = grid_node_jid

        # Battery parameters
        self.cap_kwh = float(config["STORAGE"]["CAPACITY_KWH"])
        self.soc_kwh = float(soc_init_frac) * self.cap_kwh
        self.temp_c = 25.0
        self.soh = 1.0

        # Pricing
        self.ask_price = float(config["STORAGE"]["ASK_PRICE"])
        self.price_max = float(config["STORAGE"]["MAX_PRICE"])

        # Modes
        self.emergency_only = config["STORAGE"]["EMERGENCY_ONLY"]
        self.response_probability = 1.0  # Full responsiveness

        # Auction round state
        self.active_round_id = None
        self.round_deadline_ts = 0.0

    async def setup(self):
        """
        Setup lifecycle method.

        Behaviours added:
            - InitialSetup(): Register and send initial status.
            - StartAfterDelay(): Starts periodic monitoring after delay.
            - RoundReceiver(): Reacts to CFPs and controls SOC changes.
        """
        self.add_behaviour(self.InitialSetup())
        self.add_behaviour(self.StartAfterDelay())
        self.add_behaviour(self.RoundReceiver())

    # INITIALIZATION BEHAVIOURS

    class InitialSetup(OneShotBehaviour):
        """
        Sends registration and initial battery status immediately at startup.
        """

        async def run(self):
            # Register storage unit
            register_msg = Message(to=self.agent.grid_node_jid)
            register_msg.metadata = {
                "performative": "inform",
                "type": "register_storage",
            }
            register_msg.body = json.dumps({
                "jid": str(self.agent.jid),
                "capacity_kwh": self.agent.cap_kwh,
                "emergency_only": self.agent.emergency_only,
                "timestamp": time.time()
            })
            await self.send(register_msg)

            # Send initial status
            status_msg = Message(to=self.agent.grid_node_jid)
            status_msg.metadata = {"performative": "inform", "type": "statusBattery"}
            status_msg.body = json.dumps({
                "jid": str(self.agent.jid),
                "soc_kwh": self.agent.soc_kwh,
                "cap_kwh": self.agent.cap_kwh,
                "temp_c": self.agent.temp_c,
                "soh": self.agent.soh,
                "emergency_only": self.agent.emergency_only,
                "timestamp": time.time()
            })
            await self.send(status_msg)

    class StartAfterDelay(OneShotBehaviour):
        """
        Waits a fixed delay (30 seconds) before starting the periodic monitor.
        """

        def __init__(self):
            super().__init__()
            self.delay_seconds = 30.0

        async def run(self):
            await asyncio.sleep(self.delay_seconds)
            self.agent.add_behaviour(self.agent.Monitor(period=30.0))

    class Monitor(PeriodicBehaviour):
        """
        Periodically sends SOC, SOH, temperature, and status every 30 seconds.
        """

        async def run(self):
            msg = Message(to=self.agent.grid_node_jid)
            msg.metadata = {"performative": "inform", "type": "statusBattery"}
            msg.body = json.dumps({
                "jid": str(self.agent.jid),
                "soc_kwh": self.agent.soc_kwh,
                "cap_kwh": self.agent.cap_kwh,
                "temp_c": self.agent.temp_c,
                "soh": self.agent.soh,
                "emergency_only": self.agent.emergency_only,
                "timestamp": time.time()
            })
            await self.send(msg)

    # ROUND RECEIVER (CFP, ACCEPTS, CONTROL COMMANDS)

    class RoundReceiver(CyclicBehaviour):
        """
        Listens for:
            - call_for_offers: decide whether to request/offer energy
            - control_command: SOC increases (energy purchased)
            - offer_accept: SOC decreases (energy sold)

        Emergency mode logic:
            - Only offers energy when producers fail AND SOC > 20%.
            - Otherwise recharges if SOC < 99%.
        """

        async def run(self):
            msg = await self.receive(timeout=0.5)
            if not msg:
                return

            msg_type = msg.metadata.get("type", "")

            # CFP (Call for Offers)
            if msg_type == "call_for_offers":
                data = json.loads(msg.body)

                self.agent.active_round_id = data.get("round_id")
                self.agent.round_deadline_ts = data.get("deadline_ts", 0)

                producers_failed = data.get("producers_failed", False)
                soc_pct = self.agent.soc_kwh / self.agent.cap_kwh

                # EMERGENCY MODE LOGIC
                if self.agent.emergency_only:
                    if producers_failed and soc_pct > 0.20:
                        self.agent.add_behaviour(self.agent.OfferEnergyEmergency())
                    elif soc_pct < 0.99:
                        self.agent.add_behaviour(self.agent.RequestEnergy())

                # NORMAL MODE
                else:
                    if soc_pct < 0.95:
                        self.agent.add_behaviour(self.agent.RequestEnergy())
                    else:
                        self.agent.add_behaviour(self.agent.OfferEnergy())

            # CONTROL COMMAND (energy delivered to storage)
            elif msg_type == "control_command":
                data = json.loads(msg.body)
                kwh = data.get("kw", 0)
                self.agent.soc_kwh = min(self.agent.soc_kwh + kwh, self.agent.cap_kwh)

            # ACCEPTED OFFER (energy taken from storage)
            elif msg_type == "offer_accept":
                data = json.loads(msg.body)
                kwh = data.get("kw", 0)
                self.agent.soc_kwh = max(self.agent.soc_kwh - kwh, 0)

    # ENERGY REQUEST / OFFER BEHAVIOURS

    class RequestEnergy(OneShotBehaviour):
        """
        Requests energy from GridNode whenever SOC < target threshold.

        Behaviour:
            - Computes needed energy.
            - Sends energy_request with max acceptable price.
        """

        async def run(self):
            R = self.agent.active_round_id
            if not R:
                return

            needed = self.agent.cap_kwh - self.agent.soc_kwh

            if needed > 0.5:
                base_price_max = self.agent.price_max
                price_variation = random.uniform(-0.02, 0.02)
                final_price_max = base_price_max * (1 + price_variation)

                msg = Message(to=self.agent.grid_node_jid)
                msg.metadata = {"performative": "request", "type": "energy_request"}
                msg.body = json.dumps({
                    "round_id": R,
                    "need_kwh": needed,
                    "price_max": round(final_price_max, 2)
                })
                await self.send(msg)

    class OfferEnergyEmergency(OneShotBehaviour):
        """
        Emergency mode offer:
            - Sends ALL available energy except the 20% reserve.
            - Price is fixed, no randomness.
            - Ignores normal SOC upper limits.
        """

        async def run(self):
            R = self.agent.active_round_id
            if not R:
                return

            min_reserve = 0.20 * self.agent.cap_kwh
            available = self.agent.soc_kwh - min_reserve

            if available > 0.5:
                now = time.time()
                if now <= self.agent.round_deadline_ts:
                    msg = Message(to=self.agent.grid_node_jid)
                    msg.metadata = {"performative": "propose", "type": "energy_offer"}
                    msg.body = json.dumps({
                        "round_id": R,
                        "offer_kwh": available,
                        "price": self.agent.ask_price,
                        "emergency": True
                    })
                    await self.send(msg)

    class OfferEnergy(OneShotBehaviour):
        """
        Normal mode energy selling behaviour.

        Conditions:
            - SOC must be above minimum reserve.
            - Random refusal possible.
            - Adds slight price variation (±1%).
        """

        async def run(self):
            R = self.agent.active_round_id
            if not R:
                return

            # Refusal for negotiation behaviour
            if random.random() > self.agent.response_probability:
                decline_msg = Message(to=self.agent.grid_node_jid)
                decline_msg.metadata = {
                    "performative": "refuse",
                    "type": "declined_offer"
                }
                decline_msg.body = json.dumps({"round_id": R, "reason": "agent_decision"})
                await self.send(decline_msg)
                return

            # Compute available energy
            available = self.agent.soc_kwh - 0.20 * self.agent.cap_kwh
            if available > 0:
                now = time.time()
                if now <= self.agent.round_deadline_ts:
                    base_price = self.agent.ask_price
                    price_variation = random.uniform(-0.01, 0.01)
                    final_price = base_price * (1 + price_variation)

                    msg = Message(to=self.agent.grid_node_jid)
                    msg.metadata = {"performative": "propose", "type": "energy_offer"}
                    msg.body = json.dumps({
                        "round_id": R,
                        "offer_kwh": available,
                        "price": round(final_price, 2)
                    })
                    await self.send(msg)
