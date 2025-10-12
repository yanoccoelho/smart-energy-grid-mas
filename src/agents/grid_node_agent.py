import spade

class GridNodeAgent(spade.agent.Agent):
    """
    Criado só para testar, sem nada complexo por agora.
    """
    async def setup(self):
        jid_local_part = str(self.jid).split('@')[0]
        print(f"[{jid_local_part}] Grid Node Agent starting and ready.")