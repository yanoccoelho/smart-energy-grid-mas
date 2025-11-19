import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
import os

# Load CSV
file_path = "metrics_logs/final.csv"
df = pd.read_csv(file_path)

df['round'] = df['round'].astype(int)

# Create output folder
os.makedirs("graphics", exist_ok=True)

# ---------------------------------------------------
# Delete old graphics before generating new ones
# ---------------------------------------------------
for filename in os.listdir("graphics"):
    file_path = os.path.join("graphics", filename)
    try:
        if os.path.isfile(file_path):
            os.remove(file_path)
    except Exception as e:
        print(f"Erro ao apagar {file_path}: {e}")


# ---------------------------------------------------
# 1) Compute hour of day for each round
# ---------------------------------------------------
start_datetime = datetime.strptime("2025-01-01 07:00", "%Y-%m-%d %H:%M")
df["datetime"] = df["round"].apply(lambda r: start_datetime + timedelta(hours=r))
df["hour"] = df["datetime"].dt.hour  # 0–23

# ---------------------------------------------------
# 2) Compute average of all numeric columns grouped by hour
# ---------------------------------------------------
hourly_avg = df.groupby("hour").mean(numeric_only=True)

# ---------------------------------------------------
# Helper to save plots
# ---------------------------------------------------
def save_plot(fig, filename):
    fig.savefig(f"graphics/{filename}", bbox_inches='tight', dpi=200)
    plt.close(fig)


# ===================================================
# PLOT 1 — Demand, Supplied, Wasted Energy (kWh)
# ===================================================
fig = plt.figure(figsize=(12, 5))
plt.plot(hourly_avg.index, hourly_avg["total_demand"], label="Avg Demand (kWh)")
plt.plot(hourly_avg.index, hourly_avg["total_supplied"], label="Avg Supplied (kWh)")
plt.plot(hourly_avg.index, hourly_avg["wasted_energy"], label="Avg Wasted Energy (kWh)", linestyle="--", color="orange")

plt.xlabel("Hour of Day")
plt.ylabel("Energy (kWh)")
plt.title("Hourly Average — Demand, Supplied & Wasted Energy")
plt.xticks(range(0, 24))
plt.grid()
plt.legend()
save_plot(fig, "01_hourly_energy_profile.png")



# ===================================================
# PLOT 2A — Market Value (€)
# ===================================================
fig = plt.figure(figsize=(12, 5))
plt.plot(hourly_avg.index, hourly_avg["market_value"], label="Avg Market Value (€)", color="green")
plt.xlabel("Hour of Day")
plt.ylabel("Money (€)")
plt.title("Hourly Average — Market Value")
plt.xticks(range(0, 24))
plt.grid()
plt.legend()
save_plot(fig, "02a_hourly_market_value.png")



# ===================================================
# PLOT 3A — External Grid Energy Flow (kWh)
# ===================================================
fig = plt.figure(figsize=(12, 5))
plt.plot(hourly_avg.index, hourly_avg["ext_grid_sold"], label="Sold to Grid (kWh)")
plt.plot(hourly_avg.index, hourly_avg["ext_grid_bought"], label="Bought from Grid (kWh)")
plt.xlabel("Hour of Day")
plt.ylabel("Energy (kWh)")
plt.title("Hourly Average — External Grid Energy Flow")
plt.xticks(range(0, 24))
plt.grid()
plt.legend()
save_plot(fig, "03a_hourly_ext_grid_energy.png")


# ===================================================
# PLOT 3B — External Grid Financial Flow (€)
# ===================================================
fig = plt.figure(figsize=(12, 5))
plt.plot(hourly_avg.index, hourly_avg["ext_grid_sold_value"], label="Grid Sold Value (€)")
plt.plot(hourly_avg.index, hourly_avg["ext_grid_bought_value"], label="Grid Bought Cost (€)")
plt.xlabel("Hour of Day")
plt.ylabel("Money (€)")
plt.title("Hourly Average — External Grid Financial Flow")
plt.xticks(range(0, 24))
plt.grid()
plt.legend()
save_plot(fig, "03b_hourly_ext_grid_financial.png")


# ===================================================
# PLOT 4 — Fulfillment (%) & Houses Without Power
# ===================================================
fig = plt.figure(figsize=(12, 5))
plt.plot(hourly_avg.index, hourly_avg["avg_fulfillment"], label="Avg Fulfillment (%)")
plt.plot(hourly_avg.index, hourly_avg["houses_without_power"], label="Avg Houses w/out Power", color="red")
plt.xlabel("Hour of Day")
plt.ylabel("Average (%) / Houses")
plt.title("Hourly Average — Fulfillment & Houses Without Power")
plt.xticks(range(0, 24))
plt.grid()
plt.legend()
save_plot(fig, "04_hourly_fulfillment_and_outages.png")


# ===================================================
# PLOT 5 — Reliability Indicators (0–1)
# ===================================================
fig = plt.figure(figsize=(12, 5))
plt.plot(hourly_avg.index, hourly_avg["blackout"], label="Blackout Probability")
plt.plot(hourly_avg.index, hourly_avg["blackout_impacted"], label="Blackout Impacted Rate")
plt.plot(hourly_avg.index, hourly_avg["any_producer_failed"], label="Producer Failure Rate")
plt.plot(hourly_avg.index, hourly_avg["emergency_used"], label="Emergency Activation Rate")
plt.xlabel("Hour of Day")
plt.ylabel("Probability (0–1)")
plt.title("Hourly Average — System Reliability Indicators")
plt.xticks(range(0, 24))
plt.grid()
plt.legend()
save_plot(fig, "05_hourly_reliability.png")