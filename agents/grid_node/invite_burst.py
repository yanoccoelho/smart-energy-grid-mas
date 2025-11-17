from spade.behaviour import OneShotBehaviour
from spade.message import Message
import json

class InviteBurstSend(OneShotBehaviour):
    """
    Behaviour that sends a "call for offers" (CFP) message burst to
    all eligible agents for the current round.
    """

    def __init__(self, round_id, seller_jids, deadline_ts, producers_failed=False):
        """
        Initialize the _InviteBurstSend behaviour.

        Args:
            round_id (float): Identifier of the round.
            seller_jids (list[str]): List of agent JIDs to invite.
            deadline_ts (float): UNIX timestamp representing the
                deadline for sending offers.
            producers_failed (bool): Indicates whether any producer
                is currently in a failure state (used as contextual info).
        """
        super().__init__()
        self.round_id = round_id
        self.seller_jids = seller_jids
        self.deadline_ts = deadline_ts
        self.producers_failed = producers_failed

    async def run(self):
        """
        Send CFP messages to all target agents, including round id,
        deadline, and whether producers have failed.
        """
        for jid in self.seller_jids:
            msg = Message(to=jid)
            msg.metadata = {"performative": "cfp", "type": "call_for_offers"}
            msg.body = json.dumps(
                {
                    "round_id": self.round_id,
                    "deadline_ts": self.deadline_ts,
                    "producers_failed": self.producers_failed,
                }
            )
            await self.send(msg)
