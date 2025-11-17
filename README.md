# Smart Energy Grid – Multi-Agent System (SPADE)

A decentralized smart energy grid simulation built using **SPADE**, where autonomous agents
represent households, prosumers, renewable energy producers, storage units, and grid nodes.
The system balances energy supply/demand dynamically, adapts to failures, and executes
peer-to-peer energy negotiations—without a central controller.


## Assignment Overview

This project implements a **Multi-Agent Smart Energy Grid Management System** as described in the coursework assignment.  

The goal is to simulate a **city-wide decentralized energy grid**, where autonomous agents
collaborate to:

- Balance energy supply and demand  
- Negotiate energy exchanges (Contract Net Protocol)  
- Manage renewable generation variability  
- Handle battery storage and failures  
- Ensure efficiency, fairness, and resilience  

All decisions emerge from **agent interaction**, not a centralized controller.

## Core Agent Types

### Household Agents
- Represent energy consumers  
- Some households include PV solar + battery → **Prosumers**  
- Make requests when in deficit  
- Auction surplus energy when available  
- Autonomous demand, production, storage, and bidding behavior  

---

### Producer Agents
- Represent **solar farms** and **wind turbines**  
- Output varies with:
  - Solar irradiance  
  - Wind speed  
  - Time of day  
  - Random variability  
- Can suffer **operational failures** and recover autonomously  

---

### Grid Node Agents
- Balance local supply & demand  
- Run auctions using **Contract Net Protocol**  
- Coordinate with external grid  
- Track performance metrics  
- Route energy between agents  

---

### Storage Manager Agent
- Represents a **large community battery (50 kWh)**  
- Operates in:
  - **Normal mode**, offering/buying energy when SOC thresholds are met  
  - **Emergency mode**, supplying energy when producers fail  
- Maintains State of Charge (SOC), SOH, temperature  

---

### Environment Agent
- Simulates dynamic environmental variables:
  - Solar irradiance
  - Wind speed
  - Temperature
  - Simulated hour of day  
- Broadcasts updates to consumers and producers  
- Drives variability in renewable production  

---

## Protocols & Behaviors

### Contract Net Protocol (CNP)
- **Call for Proposals (CFP)** sent by Grid Node  
- Producers/Prosumers respond with:
  - Energy offers  
  - Prices  
- Grid Node selects best offers & sends accept/reject  

### Peer-to-peer negotiation  
Households negotiate deficit/surplus autonomously.

### Storage and emergency fallback  
If producers fail, the storage unit supplies the grid.

---

## Performance Metrics Collected

- % of demand covered  
- Energy wasted (unused surplus)  
- Fairness across households  
- External grid dependency  
- Market value traded  
- Frequency of blackouts  
- Number of producer failures  
- Emergency activations  

---

## Repository Structure

```bash
project/
│
├── agents/
│ ├── environment_agent.py
│ ├── grid_node_agent.py 
│ ├── performance_metrics.py
│ ├── producer_agent.py
│ ├── household_agent.py
│ └── storage_manager_agent.py
│
├── scenarios/
│ ├── base_config.py
│ ├── blackout.py 
│ ├── grid_failure.py
│ ├── high_demand.py
│ ├── low_demand.py
│ ├── overproduction.py 
│ ├── producer_failure.py 
│ └── storm.py
│
├── logs/
│ ├── agents_logs.db
│ ├── db_logger.py
│ └── inspect_db.py
│
├── main.py
├── requirements.txt
└── README.md
```

---

## Installation & Setup

### 1️. Clone the Repository

```bash
git clone https://github.com/yanoccoelho/smart-energy-grid-mas.git
cd smart-energy-grid-mas
```

### 2. Create virtual environment
The Python version used in the project was [Python 3.10.11](https://www.python.org/downloads/release/python-31011). Make sure you have this version installed.

```bash
python -m venv==3.10.11 venv
```

### 3. Activate the environment

Windows:
``` bash
venv\Scripts\activate
```

Mac/Linux:
``` bash
source venv/bin/activate
```

### 4. Install dependencies
``` bash
pip install -r requirements.txt
```

### 5. Running the Simulation

The system requires two terminals, both inside the virtual environment.

- Terminal 1 — Start the SPADE XMPP server

    ``` bash
    spade run
    ```

- Terminal 2 — Start the simulation

    ``` bash
    python main.py
    ```


You will be asked to:

- Choose a scenario

- Optionally override number of consumers/prosumers

Then all agents start automatically

### 6.Inspecting the Database Logs

All events, offers, auctions, failures, metrics, and energy exchanges are stored in:

`logs/agents_logs.db`

To inspect:

``` bash
python inspect_db.py
```

## Authors

- [Guilherme Klippel](https://github.com/Klippell)
- [Isabela Cartaxo](https://github.com/belacartaxo)
- [Yan Coelho](https://github.com/yanoccoelho)
