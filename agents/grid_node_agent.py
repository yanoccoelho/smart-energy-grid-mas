import time
import random
import spade
from collections import defaultdict
from logs.db_logger import DBLogger
from agents.performance_metrics import PerformanceTracker
from scenarios.base_config import SCENARIO_CONFIG
from agents.grid_node.receivers import Receiver
from agents.grid_node.startup import StartupCoordinator
from agents.grid_node.orchestrator import RoundOrchestrator
from agents.grid_node.print_status import PrintAgentStatus
from agents.grid_node.print_totals import PrintTotalsTable
from agents.grid_node.invite_burst import InviteBurstSend



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
        self.agent_limits_kw = self.config["SIMULATION"].get("AGENT_LIMITS_KW", {})
        self.transmission_limit_kw = self.config["SIMULATION"]["TRANSMISSION_LIMIT_KW"]

        if external_grid_config is None:
            external_grid_config = {
                "enabled": True,
                "buy_price_min": config["EXTERNAL_GRID"]["MIN_DYNAMIC_PRICE"],
                "buy_price_max": config["EXTERNAL_GRID"]["MAX_DYNAMIC_PRICE"],
                "sell_price_min": config["EXTERNAL_GRID"]["SELL_PRICE"],
                "sell_price_max": config["EXTERNAL_GRID"]["BUY_PRICE"],
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
        self.sim_hour = 1
        self.sim_day = 1
        self.round_counter = 1
        self.current_solar = 0.0
        self.current_wind = 0.0
        self.current_temp = 20.0

        self.add_behaviour(Receiver())
        self.add_behaviour(StartupCoordinator())

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

    def _infer_agent_category(self, agent_jid):
        """
        Infer the type of an agent (consumer, prosumer, producer, storage).
        """
        state = self.households_state.get(agent_jid)
        if state:
            return "prosumer" if state.get("is_prosumer", False) else "consumer"

        if agent_jid in self.known_households:
            if "prosumer" in agent_jid.lower():
                return "prosumer"
            return "consumer"

        if agent_jid in self.producers_state or agent_jid in self.known_producers:
            return "producer"

        if agent_jid in self.storage_state or agent_jid in self.known_storage:
            return "storage"

        return None

    def get_agent_limit_kw(self, agent_jid, default=None):
        """
        Return the configured power limit for the given agent, if any.
        """
        limits = self.agent_limits_kw or {}
        category = self._infer_agent_category(agent_jid)
        if not category:
            return default

        if category == "storage":
            limit = limits.get("storage")
            if limit is None:
                limit = limits.get("battery")
        else:
            limit = limits.get(category)

        if limit is None:
            return default

        try:
            return float(limit)
        except (TypeError, ValueError):
            return default

    def _estimate_prosumer_internal_use(self, agent_jid):
        """
        Estimate how much energy a prosumer is retaining internally (local load +
        battery charging) during this round.
        """
        state = self.households_state.get(agent_jid)
        if not state or not state.get("is_prosumer", False):
            return 0.0

        production = float(state.get("prod_kwh", 0.0))
        demand = float(state.get("demand_kwh", 0.0))
        local_usage = min(production, demand)

        net = production - demand
        battery_charge = 0.0
        if net > 0.0:
            battery_level = float(state.get("battery_kwh", 0.0))
            battery_capacity = float(
                self.config["HOUSEHOLDS"]["BATTERY_CAPACITY_KWH"]
            )
            charge_rate = float(self.config["HOUSEHOLDS"]["BATTERY_CHARGE_RATE_KW"])
            remaining_capacity = max(0.0, battery_capacity - battery_level)
            if remaining_capacity > 0.0:
                battery_charge = min(net, charge_rate, remaining_capacity)

        internal_use = local_usage + battery_charge
        return max(0.0, internal_use)

    def get_operational_limit_info(self, agent_jid, role):
        """
        Return a dict describing the effective per-round limit for an agent.

        The dict contains:
            - base_limit: configured limit (may be None).
            - effective_limit: limit after subtracting internal usage.
            - display: formatted string for logs (if applicable).
            - internal_use: kWh withheld for internal purposes (prosumer only).
        """
        base_limit = self.get_agent_limit_kw(agent_jid)
        info = {
            "base_limit": base_limit,
            "effective_limit": base_limit,
            "display": None,
            "internal_use": 0.0,
        }

        if base_limit is None:
            return info

        category = self._infer_agent_category(agent_jid)
        if category == "prosumer":
            internal = self._estimate_prosumer_internal_use(agent_jid)
            effective = max(0.0, base_limit - internal)
            info.update(
                {
                    "effective_limit": effective,
                    "internal_use": internal,
                    "display": f"limit({base_limit:.1f} - {internal:.1f} = {effective:.1f})",
                }
            )
        else:
            info["display"] = f"limit {base_limit:.1f} kWh"

        return info

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
        if 18 <= hour < 22:
            return "High Demand - Evening Peak"
        if 22 <= hour < 24 or 0 <= hour < 6:
            return "Low Demand - Night Off-Peak"
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
                        f"\n⚠️ SYSTEM ALERT: {p_jid} failed (offline for {failure_duration} rounds)."
                    )
                    print("⚡ Emergency backup activated: storage will cover the deficit.\n")
                    self.any_producer_failed = True
                    break
