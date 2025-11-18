from collections import defaultdict
from scenarios.base_config import SCENARIO_CONFIG


class PerformanceTracker:
    """
    Tracks global and periodic performance metrics of the microgrid system.

    This class collects operational data each round and prints aggregated
    performance summaries every N rounds (defined in scenario configuration).

    The tracker monitors:
        - Total and recent energy demand
        - Energy supplied by the microgrid and the external grid
        - Market value generated internally
        - External grid transactions (kWh and monetary value)
        - Buyer fulfillment percentages
        - Blackout statistics (full, partial, perfect rounds)
        - Emergency activations and producer failures

    Args:
        config (dict): Global scenario configuration dictionary.

    Attributes:
        rounds_data (list): List of dictionaries, each representing a round.
        total_demand_kwh (float): Cumulative energy demand.
        total_supplied_kwh (float): Cumulative energy supplied.
        total_market_value (float): Cumulative internal market value.
        ext_grid_supplied_kwh (float): Total kWh imported from external grid.
        ext_grid_bought_kwh (float): Total kWh exported to external grid.
        ext_grid_sold_value (float): Total revenue from selling to external grid.
        ext_grid_bought_value (float): Total cost from buying from external grid.
        household_fulfillment (defaultdict): Per-household fulfillment history.
        rounds_full_blackout (int): Count of rounds with 0% demand met.
        rounds_partial_blackout (int): Count of rounds with partial fulfillment.
        rounds_perfect (int): Count of rounds with 100% demand met.
        producer_failures (int): Number of rounds where producers failed.
        emergency_activations (int): Number of emergency mode activations.
        report_interval (int): Number of rounds between summary reports.
    """

    def __init__(self, config=SCENARIO_CONFIG):
        # Round history
        self.rounds_data = []

        # Cumulative totals
        self.total_demand_kwh = 0.0
        self.total_supplied_kwh = 0.0
        self.total_market_value = 0.0
        self.ext_grid_supplied_kwh = 0.0
        self.ext_grid_bought_kwh = 0.0
        self.ext_grid_sold_value = 0.0
        self.ext_grid_bought_value = 0.0

        # Household-level metrics
        self.household_fulfillment = defaultdict(list)
        self.rounds_blackout = 0
        self.rounds_normal = 0

        # Emergency & failure tracking
        self.producer_failures = 0
        self.emergency_activations = 0

        # Configurable reporting interval
        self.report_interval = config["METRICS"]["REPORT_INTERVAL_ROUNDS"]

    def record_round(self, round_num, round_data):
        """
        Records the performance metrics of a simulation round.

        Args:
            round_num (int): The current round index (starting at 1).
            round_data (dict): Dictionary containing:
                - total_demand (float)
                - total_supplied (float)
                - market_value (float)
                - wasted_energy (float)
                - ext_grid_sold (float)
                - ext_grid_bought (float)
                - ext_grid_sold_value (float)
                - ext_grid_bought_value (float)
                - buyer_fulfillment (dict[str, float])
                - any_producer_failed (bool)
                - emergency_used (bool)
        """
        self.rounds_data.append(round_data)

        # Update cumulative metrics
        self.total_demand_kwh += round_data.get("total_demand", 0)
        self.total_supplied_kwh += round_data.get("total_supplied", 0)
        self.total_market_value += round_data.get("market_value", 0)

        self.ext_grid_supplied_kwh += round_data.get("ext_grid_sold", 0)
        self.ext_grid_bought_kwh += round_data.get("ext_grid_bought", 0)

        self.ext_grid_sold_value += round_data.get("ext_grid_sold_value", 0)
        self.ext_grid_bought_value += round_data.get("ext_grid_bought_value", 0)

        # Buyer fulfillment tracking
        buyer_fulfillment = round_data.get("buyer_fulfillment", {})
        for household, pct in buyer_fulfillment.items():
            self.household_fulfillment[household].append(pct)

        avg_fulfillment = round_data.get("avg_fulfillment")
        if avg_fulfillment is None:
            avg_fulfillment = (
                sum(buyer_fulfillment.values()) / len(buyer_fulfillment)
                if buyer_fulfillment
                else 0
            )
            round_data["avg_fulfillment"] = avg_fulfillment

        blackout = round_data.get("blackout")
        if blackout is None:
            blackout = avg_fulfillment < 99.0
            round_data["blackout"] = blackout

        if blackout:
            self.rounds_blackout += 1
        else:
            self.rounds_normal += 1

        # Failures and emergencies
        if round_data.get("any_producer_failed", False):
            self.producer_failures += 1

        if round_data.get("emergency_used", False):
            self.emergency_activations += 1

        # Print periodic report
        if (
            self.report_interval > 0
            and round_num > 0
            and round_num % self.report_interval == 0
        ):
            self.print_periodic_summary(round_num)

    def print_periodic_summary(self, round_num):
        """
        Prints a summary of the last N rounds (defined by report_interval).

        Args:
            round_num (int): Current simulation round.
        """
        start_idx = max(0, round_num - self.report_interval)
        recent_data = self.rounds_data[start_idx:round_num]

        if not recent_data:
            return

        # Aggregate metrics for the period
        recent_demand = sum(r.get("total_demand", 0) for r in recent_data)
        recent_supplied = sum(r.get("total_supplied", 0) for r in recent_data)
        recent_wasted = sum(r.get("wasted_energy", 0) for r in recent_data)
        recent_value_microgrid = sum(r.get("market_value", 0) for r in recent_data)

        recent_ext_grid_sold = sum(r.get("ext_grid_sold", 0) for r in recent_data)
        recent_ext_grid_bought = sum(r.get("ext_grid_bought", 0) for r in recent_data)

        recent_ext_sold_value = sum(r.get("ext_grid_sold_value", 0) for r in recent_data)
        recent_ext_bought_value = sum(r.get("ext_grid_bought_value", 0) for r in recent_data)

        recent_blackouts = sum(1 for r in recent_data if r.get("blackout"))

        # Percentages
        fulfillment_pct = (recent_supplied / recent_demand * 100) if recent_demand > 0 else 0
        from_microgrid = recent_supplied - recent_ext_grid_sold
        microgrid_pct = (from_microgrid / recent_supplied * 100) if recent_supplied > 0 else 0
        ext_grid_pct = (recent_ext_grid_sold / recent_supplied * 100) if recent_supplied > 0 else 0

        # Net balances
        net_balance_period = recent_ext_sold_value - recent_ext_bought_value
        net_balance_cumulative = self.ext_grid_sold_value - self.ext_grid_bought_value

        print("\n" + "━" * 80)
        print(f"  📊 PERFORMANCE SUMMARY (Rounds {start_idx + 1}-{round_num})")
        print("━" * 80)

        print("  ⚡ Energy Flow:")
        print(
            f"     • Demand: {recent_demand:.1f} kWh | Supplied: {recent_supplied:.1f} kWh "
            f"({fulfillment_pct:.1f}%)"
        )
        print(f"     • Microgrid: {from_microgrid:.1f} kWh ({microgrid_pct:.1f}%)")
        print(f"     • External Grid: {recent_ext_grid_sold:.1f} kWh ({ext_grid_pct:.1f}%)")

        print("\n  💰 Economic Performance:")
        print(f"     • Market Value: €{recent_value_microgrid:.2f}")
        print(
            f"     • Sold → External Grid: {recent_ext_grid_bought:.1f} kWh "
            f"(€{recent_ext_sold_value:.2f})"
        )
        print(
            f"     • Bought ← External Grid: {recent_ext_grid_sold:.1f} kWh "
            f"(€{recent_ext_bought_value:.2f})"
        )

        print("\n  🚨 Reliability:")
        print(
            f"     • Blackouts this period: {recent_blackouts}"
        )
        print(
            f"     • Blackouts Totals: {self.rounds_blackout}"
        )

        # Period balance
        print("\n  ⚖️  Net Balance (Period): ", end="")
        if net_balance_period > 0:
            print(f"+€{net_balance_period:.2f} (export surplus)")
        elif net_balance_period < 0:
            print(f"-€{abs(net_balance_period):.2f} (import dependency)")
        else:
            print("€0.00 (self-sufficient)")

        # Cumulative balance
        print("  📉 Net Balance (Total): ", end="")
        if net_balance_cumulative > 0:
            print(f"+€{net_balance_cumulative:.2f} (export surplus)")
        elif net_balance_cumulative < 0:
            print(f"-€{abs(net_balance_cumulative):.2f} (import dependency)")
        else:
            print("€0.00 (self-sufficient)")

        print("━" * 80 + "\n")
