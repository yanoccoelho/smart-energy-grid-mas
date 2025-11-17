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