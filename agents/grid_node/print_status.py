from spade.behaviour import OneShotBehaviour


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
        print("\n--- AGENT STATUS REPORTS ---\n")

        producers_cfg = self.agent.config.get("PRODUCERS", {})
        solar_capacity_kw = producers_cfg.get("SOLAR_CAPACITY_KW", 0.0)
        wind_capacity_kw = producers_cfg.get("WIND_CAPACITY_KW", 0.0)

        def limit_suffix(agent_jid):
            limit = self.agent.get_agent_limit_kw(agent_jid)
            if limit is None:
                return ""
            return f" | Limit = {limit:.1f} kWh"

        consumers = []
        prosumers = []

        for jid, state in self.agent.households_state.items():
            is_prosumer = state.get("is_prosumer", False)
            if is_prosumer:
                prosumers.append((jid, state))
            else:
                consumers.append((jid, state))

        print("CONSUMERS")
        for jid, state in consumers:
            demand = round(state.get("demand_kwh", 0), 2)
            deficit = -demand
            print(
                f"   {jid}: Demand = {demand:.2f} kWh | "
                f"Deficit = {deficit:.2f} kWh"
                f"{limit_suffix(jid)}"
            )

        print("\nPROSUMERS")
        for jid, state in prosumers:
            demand = round(state.get("demand_kwh", 0), 2)
            prod = round(state.get("prod_kwh", 0), 2)
            net = prod - demand
            status = "Surplus" if net > 0 else "Deficit"
            solar = state.get("solar_irradiance", 0)
            area = state.get("panel_area_m2", 0)

            print(
                f"   {jid}: Demand = {demand:.2f} kWh | "
                f"Production = {prod:.2f} kWh | {status} = {net:+.2f} kWh"
                f"{limit_suffix(jid)}"
            )
            print(
                f"           Solar: {solar:.2f} | Area: {area:.1f} m² "
                f"-> {prod:.2f} kWh"
            )

        print("\nPRODUCERS")
        for jid, state in self.agent.producers_state.items():
            prod = round(state.get("prod_kwh", 0), 2)
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
                    f"  {jid}: Production = {prod:.2f} kWh ({status}) [FAILURE]"
                    f"{limit_suffix(jid)}"
                )
                continue

            status = "Available" if prod > 0 else "Offline"

            if prod_type == "solar":
                print(
                    f"  {jid}: Production = {prod:.2f} kWh ({status})"
                    f"{limit_suffix(jid)}"
                )
                if prod > 0:
                    print(
                        f"           Solar: {solar:.2f} x {solar_capacity_kw:.1f} kW "
                        f"= {prod:.2f} kWh"
                    )
            elif prod_type == "wind":
                print(
                    f"  {jid}: Production = {prod:.2f} kWh ({status})"
                    f"{limit_suffix(jid)}"
                )
                if prod > 0:
                    print(
                        f"           Wind: {wind:.1f} m/s x {wind_capacity_kw:.1f} kW "
                        f"= {prod:.2f} kWh"
                    )
            else:
                print(
                    f"  {jid}: Production = {prod:.2f} kWh ({status})"
                    f"{limit_suffix(jid)}"
                )

        print("\nSTORAGE")
        for jid, state in self.agent.storage_state.items():
            soc = round(state.get("soc_kwh", 0), 2)
            cap = round(state.get("cap_kwh", 1), 2)
            pct = 100 * soc / cap if cap > 0 else 0
            avail = max(0.0, soc - 0.2 * cap)
            emergency_only = state.get("emergency_only", False)

            if emergency_only:
                if self.agent.any_producer_failed:
                    print(
                        f"   {jid}: SOC = {soc:.2f}/{cap:.2f} kWh "
                        f"({pct:.0f}%) | Available: {avail:.2f} kWh "
                        "(emergency mode supplying)"
                        f"{limit_suffix(jid)}"
                    )
                else:
                    print(
                        f"   {jid}: SOC = {soc:.2f}/{cap:.2f} kWh "
                        f"({pct:.0f}%) | EMERGENCY RESERVE"
                        f"{limit_suffix(jid)}"
                    )
            else:
                print(
                    f"   {jid}: SOC = {soc:.2f}/{cap:.2f} kWh "
                    f"({pct:.0f}%) | Available: {avail:.2f} kWh"
                    f"{limit_suffix(jid)}"
                )

        print()
