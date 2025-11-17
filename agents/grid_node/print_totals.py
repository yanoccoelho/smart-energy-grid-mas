from spade.behaviour import OneShotBehaviour

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
