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