import spade
import asyncio
import random
from datetime import datetime
from spade.behaviour import PeriodicBehaviour

class HouseholdAgent(spade.agent.Agent):
    """
    Representa uma residência que consome energia e pode, ou não,
    produzir e armazenar energia.
    """
    
    def __init__(self, jid, password, grid_node_jid, is_prosumer=False):
        super().__init__(jid, password)
        self.grid_node_jid = grid_node_jid
        self.is_prosumer = is_prosumer
        
        # Atributos de estado do agente
        self.current_demand_kw = 0.0
        self.current_production_kw = 0.0
        self.battery_kwh = 0.0
        self.battery_capacity_kwh = 10.0 if is_prosumer else 0.0
        
    class UpdateStateBehaviour(PeriodicBehaviour):
        """"
        Comportamento que atualiza o estado energético da residência para
        simular o passar do tempo.
        """
        async def run(self):
            # Simulador de consumo (variando com a hora do dia)
            current_hour = datetime.now().hour
            if 0 <= current_hour < 6:
                self.agent.current_demand_kw = random.uniform(0.5, 1.5)
            elif 6 <= current_hour <= 9:
                self.agent.current_demand_kw = random.uniform(2.0, 4.0)
            elif 18 <= current_hour <= 22:
                self.agent.current_demand_kw = random.uniform(3.0, 5.0)
            else:
                self.agent.current_demand_kw = random.uniform(1.0, 2.5)
                
            # Simulador de produção de energia (se for prosumer)
            if self.agent.is_prosumer:
                if 7 <= current_hour <= 19:
                    # Simula uma curva de produção solar
                    peak_hour = 13
                    factor = max(0, 1 - (abs(current_hour - peak_hour) / 6)**2)
                    self.agent.current_production_kw = factor * 5.0 + random.uniform(-0.5, 0.5) # Pico de 5kW
                else:
                    self.agent.current_production_kw = 0.0
                    
                # Simulador de bateria
                net_power = self.agent.current_production_kw - self.agent.current_demand_kw
                
                if net_power > 0:
                    charge_amount = min(net_power, (self.agent.battery_capacity_kwh - self.agent.battery_kwh))
                    self.agent.battery_kwh += charge_amount
                else: # Défice de energia -> Descarregar bateria
                    discharge_amount = min(abs(net_power), self.agent.battery_kwh)
                    self.agent.battery_kwh -= discharge_amount
            
            # Imprime o estado atual     
            jid_local_part = str(self.agent.jid).split('@')[0]
            print(f"--- [{jid_local_part}] Status Update ---")
            print(f"  Demand: {self.agent.current_demand_kw:.2f} kW")
            if self.agent.is_prosumer:
                print(f"  Production: {self.agent.current_production_kw:.2f} kW")
                print(f"  Battery: {self.agent.battery_kwh:.2f} / {self.agent.battery_capacity_kwh:.2f} kWh")
                
        async def setup(self):
            jid_local_part = str(self.jid).split('@')[0]
            print(f"[{jid_local_part}] Household Agent starting...")
            
            # Inicia o comportamento de atualização de estado a cada 5 segundos
            update_behaviour = self.UpdateStateBehaviour(period=5)
            self.add_behaviour(update_behaviour)
                
            