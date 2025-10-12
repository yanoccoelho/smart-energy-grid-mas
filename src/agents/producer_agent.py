import spade
import random
from datetime import datetime
from spade.behaviour import PeriodicBehaviour

class ProducerAgent(spade.agent.Agent):
    """
    Representa um produtor de energia.
    O seu comportamento de produção depende do seu tipo: 'solar' ou 'eolic'.
    """
    def __init__(self, jid, password, grid_node_jid, production_type="solar", max_capacity_kw=100.0):
        super().__init__(jid, password)
        self.grid_node_jid = grid_node_jid
        self.production_type = production_type
        self.max_capacity_kw = max_capacity_kw
        self.current_production_kw = 0.0

    class UpdateProductionBehaviour(PeriodicBehaviour):
        """
        Comportamento que atualiza a produção de energia com base no tipo de produtor.
        """
        async def run(self):
            if self.agent.production_type == "solar":
                current_hour = datetime.now().hour
                if 7 <= current_hour < 19:
                    peak_hour = 13
                    # Simula uma curva de produção solar
                    factor = max(0, 1 - (abs(current_hour - peak_hour) / 6)**2)
                    self.agent.current_production_kw = factor * self.agent.max_capacity_kw + random.uniform(-5, 5)
                else:
                    self.agent.current_production_kw = 0.0
            
            elif self.agent.production_type == "eolic":
                # Lógica para produção eólica: mais aleatória, simulando o vento.
                base_production = self.agent.max_capacity_kw * random.uniform(0.2, 0.9)
                fluctuation = self.agent.max_capacity_kw * random.uniform(-0.15, 0.15)
                self.agent.current_production_kw = base_production + fluctuation
                
                # Simula uma quebra súbita de vento (5% de chance)
                if random.random() < 0.05:
                    self.agent.current_production_kw *= 0.1

            # Garante que a produção nunca é negativa
            self.agent.current_production_kw = max(0, self.agent.current_production_kw)
            
            jid_local_part = str(self.agent.jid).split('@')[0]
            print(f"--- [{jid_local_part} ({self.agent.production_type})] Production Update ---")
            print(f"  Available Power: {self.agent.current_production_kw:.2f} kW")

    async def setup(self):
        jid_local_part = str(self.jid).split('@')[0]
        print(f"[{jid_local_part}] Producer Agent ({self.production_type}) starting...")
        
        # Inicia o comportamento de atualização de produção a cada 5 segundos
        update_behaviour = self.UpdateProductionBehaviour(period=5)
        self.add_behaviour(update_behaviour)