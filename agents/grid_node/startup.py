from spade.behaviour import OneShotBehaviour
from spade.message import Message
import asyncio
import json
from agents.grid_node.orchestrator import RoundOrchestrator


class StartupCoordinator(OneShotBehaviour):
    """
    Behaviour that waits for all expected agents to register and
    then starts the simulation rounds.
    """

    async def run(self):
        """
        Wait until all expected agents (households, producers, storage)
        are registered, then request the first environment update and
        start the round orchestrator.
        """
        while True:
            await asyncio.sleep(0.2)

            got_h = len(self.agent.known_households)
            got_p = len(self.agent.known_producers)
            got_s = len(self.agent.known_storage)

            exp_h = self.agent.expected_agents["households"]
            exp_p = self.agent.expected_agents["producers"]
            exp_s = self.agent.expected_agents["storage"]

            if got_h >= exp_h and got_p >= exp_p and got_s >= exp_s:
                break

        total = got_h + got_p + got_s
        print(f"[GridNode] All {total} agents registered.\n")

        if self.agent.external_grid_enabled:
            print("[GridNode] External grid enabled:")
            print(
                f"  - Buy price: €{self.agent.external_grid_buy_price_min:.2f}"
                f"–€{self.agent.external_grid_buy_price_max:.2f}/kWh"
            )
            print(
                f"  - Sell price: €{self.agent.external_grid_sell_price_min:.2f}"
                f"–€{self.agent.external_grid_sell_price_max:.2f}/kWh"
            )
            print(
                f"  - Availability: "
                f"{self.agent.external_grid_acceptance_prob * 100:.0f}%\n"
            )

        print("[GridNode] Requesting initial environment update...")
        update_msg = Message(to=self.agent.env_jid)
        update_msg.metadata = {
            "performative": "request",
            "type": "request_environment_update",
        }
        update_msg.body = json.dumps(
            {"command": "update", "sim_hour": self.agent.sim_hour}
        )
        await self.send(update_msg)

        await asyncio.sleep(1.0)
        print("[GridNode] Waiting for initial status reports...\n")
        await asyncio.sleep(0.5)
        print("[GridNode] Starting auction system...\n")
        self.agent.add_behaviour(RoundOrchestrator())

