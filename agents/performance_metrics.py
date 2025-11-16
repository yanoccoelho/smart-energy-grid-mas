from collections import defaultdict

class PerformanceTracker:
    """Tracks and reports system performance metrics every 5 rounds"""
    
    def __init__(self):
        # Acumuladores gerais
        self.rounds_data = []
        
        # Totais cumulativos
        self.total_demand_kwh = 0.0
        self.total_supplied_kwh = 0.0
        self.total_market_value = 0.0
        self.ext_grid_supplied_kwh = 0.0
        self.ext_grid_bought_kwh = 0.0
        self.ext_grid_sold_value = 0.0
        self.ext_grid_bought_value = 0.0
        
        # Acompanhamento dos consumidores
        self.household_fulfillment = defaultdict(list)
        self.rounds_full_blackout = 0
        self.rounds_partial_blackout = 0
        self.rounds_perfect = 0
        
        # Acompanhamento produtores e emergências
        self.producer_failures = 0
        self.emergency_activations = 0
    
    def record_round(self, round_num, round_data):
        """
        Registra os dados de uma rodada
        round_data deve conter:
        - total_demand
        - total_supplied
        - market_value
        - wasted_energy
        - ext_grid_sold
        - ext_grid_bought
        - ext_grid_sold_value
        - ext_grid_bought_value
        - buyer_fulfillment
        - any_producer_failed
        - emergency_used
        """
        self.rounds_data.append(round_data)
        
        self.total_demand_kwh += round_data.get('total_demand', 0)
        self.total_supplied_kwh += round_data.get('total_supplied', 0)
        self.total_market_value += round_data.get('market_value', 0)
        self.ext_grid_supplied_kwh += round_data.get('ext_grid_sold', 0)
        self.ext_grid_bought_kwh += round_data.get('ext_grid_bought', 0)
        self.ext_grid_sold_value += round_data.get('ext_grid_sold_value', 0)
        self.ext_grid_bought_value += round_data.get('ext_grid_bought_value', 0)
        
        buyer_fulfillment = round_data.get('buyer_fulfillment', {})
        for household, pct in buyer_fulfillment.items():
            self.household_fulfillment[household].append(pct)
        
        avg_fulfillment = sum(buyer_fulfillment.values()) / len(buyer_fulfillment) if buyer_fulfillment else 0
        if avg_fulfillment >= 99.9:
            self.rounds_perfect += 1
        elif avg_fulfillment > 0:
            self.rounds_partial_blackout += 1
        else:
            self.rounds_full_blackout += 1
        
        if round_data.get('any_producer_failed', False):
            self.producer_failures += 1
        
        if round_data.get('emergency_used', False):
            self.emergency_activations += 1
        
        # Imprime resumos periódicos a cada 5 rounds
        if round_num > 0 and round_num % 5 == 0:
            self.print_periodic_summary(round_num)
    
    def print_periodic_summary(self, round_num):
        """
        Imprime resumo das métricas agrupadas dos últimos 5 rounds
        """
        start_idx = max(0, round_num - 5)
        recent_data = self.rounds_data[start_idx:round_num]
        
        if not recent_data:
            return
        
        # Cálculos do período (últimos 5 rounds)
        recent_demand = sum(r.get('total_demand', 0) for r in recent_data)
        recent_supplied = sum(r.get('total_supplied', 0) for r in recent_data)
        recent_wasted = sum(r.get('wasted_energy', 0) for r in recent_data)
        recent_value_microgrid = sum(r.get('market_value', 0) for r in recent_data)
        recent_ext_grid_sold = sum(r.get('ext_grid_sold', 0) for r in recent_data)
        recent_ext_grid_bought = sum(r.get('ext_grid_bought', 0) for r in recent_data)
        recent_ext_sold_value = sum(r.get('ext_grid_sold_value', 0) for r in recent_data)
        recent_ext_bought_value = sum(r.get('ext_grid_bought_value', 0) for r in recent_data)
        
        fulfillment_pct = (recent_supplied / recent_demand * 100) if recent_demand > 0 else 0
        from_microgrid = recent_supplied - recent_ext_grid_sold
        microgrid_pct = (from_microgrid / recent_supplied * 100) if recent_supplied > 0 else 0
        ext_grid_pct = (recent_ext_grid_sold / recent_supplied * 100) if recent_supplied > 0 else 0
        
        # ✅ Net Balance do período
        net_balance_period = recent_ext_sold_value - recent_ext_bought_value 
        
        # ✅ Net Balance acumulado (desde o início)
        net_balance_cumulative =self.ext_grid_sold_value - self.ext_grid_bought_value
              
        print("\n" + "━" * 80)
        print(f"  📊 PERFORMANCE SUMMARY (Rounds {start_idx+1}-{round_num})")
        print("━" * 80)
        
        print(f"  ⚡ Energy Flow:")
        print(f"     Total Demand: {recent_demand:.1f} kWh | Supplied: {recent_supplied:.1f} kWh ({fulfillment_pct:.1f}%)")
        print(f"     From Microgrid: {from_microgrid:.1f} kWh ({microgrid_pct:.1f}%)")
        print(f"     From External Grid: {recent_ext_grid_sold:.1f} kWh ({ext_grid_pct:.1f}%)")
        
        print(f"\n  💰 Economic Performance:")
        print(f"     Total Market Value (Microgrid): €{recent_value_microgrid:.2f}")
        # ✅ CORRIGIDO: agora mostra kWh e valores corretos
        print(f"     Sold to External Grid: {recent_ext_grid_bought:.1f} kWh (€{recent_ext_sold_value:.2f})")
        print(f"     Bought from External Grid: {recent_ext_grid_sold:.1f} kWh (€{recent_ext_bought_value:.2f})")
        
        # ✅ Net Balance do período (últimos 5 rounds)
        if net_balance_period > 0:
            print(f"     Net Balance (Period): +€{net_balance_period:.2f} (export surplus) ✅")
        elif net_balance_period < 0:
            print(f"     Net Balance (Period): -€{abs(net_balance_period):.2f} (import dependency) ⚠️")
        else:
            print(f"     Net Balance (Period): €0.00 (self-sufficient) ✅")
        
        # ✅ Net Balance acumulado (desde round 1)
        if net_balance_cumulative > 0:
            print(f"     Net Balance (TOTAL): +€{net_balance_cumulative:.2f} (export surplus) ✅")
        elif net_balance_cumulative < 0:
            print(f"     Net Balance (TOTAL): -€{abs(net_balance_cumulative):.2f} (import dependency) ⚠️")
        else:
            print(f"     Net Balance (TOTAL): €0.00 (self-sufficient) ✅")
        
        print("━" * 80 + "\n")
