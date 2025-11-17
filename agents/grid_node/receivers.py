from spade.behaviour import CyclicBehaviour
import json
import time

class Receiver(CyclicBehaviour):
    """
    Behaviour responsible for receiving and routing all incoming messages.

    It handles:
    - Agent registration (households, producers, storage).
    - Status reports for demand, production, and storage.
    - Energy offers and requests.
    - Declined participation in auctions.
    """

    async def run(self):
        """
        Receive a single message (if available) and process it
        according to its type.
        """
        msg = await self.receive(timeout=0.5)
        if not msg:
            return

        sender = str(msg.sender).split("/")[0]
        msg_type = msg.metadata.get("type", "")

        if msg_type == "register_household":
            self.agent.known_households.add(sender)
            self.agent._add_event("register", sender, {"type": "household"})
            return

        if msg_type == "register_producer":
            self.agent.known_producers.add(sender)
            self.agent._add_event("register", sender, {"type": "producer"})
            return

        if msg_type == "register_storage":
            self.agent.known_storage.add(sender)
            self.agent._add_event("register", sender, {"type": "storage"})
            return

        if msg_type == "status_report":
            data = json.loads(msg.body)
            self.agent.households_state[sender] = data
            R = self.agent.round_id
            if R:
                self.agent.status_seen_round[R].add(sender)
            self.agent._add_event("status", sender, data)
            self.agent.current_solar = data.get("solar_irradiance", self.agent.current_solar)
            self.agent.current_wind = data.get("wind_speed", self.agent.current_wind)
            self.agent.current_temp = data.get("temperature_c", self.agent.current_temp)
            return

        if msg_type == "production_report":
            data = json.loads(msg.body)

            # Preserve failure state controlled by the GridNode
            if sender in self.agent.producers_state:
                existing_state = self.agent.producers_state[sender]

                # If the GridNode marked this producer as offline, keep it offline
                if not existing_state.get("is_operational", True):
                    remaining = existing_state.get("failure_rounds_remaining", 0)
                    if remaining > 0:
                        remaining -= 1
                        existing_state["failure_rounds_remaining"] = remaining

                        if remaining == 0:
                            existing_state["is_operational"] = True
                            data["is_operational"] = True
                            data["failure_rounds_remaining"] = 0
                            print(f"\n{sender} recovered after failure.\n")
                        else:
                            data["is_operational"] = False
                            data["failure_rounds_remaining"] = remaining
                            data["failure_rounds_total"] = existing_state.get(
                                "failure_rounds_total", 0
                            )
                            data["prod_kwh"] = 0.0
                    else:
                        existing_state["is_operational"] = True
                        data["is_operational"] = True

            self.agent.producers_state[sender] = data

            # Update any_producer_failed flag based on all producers
            self.agent.any_producer_failed = False
            for _, state in self.agent.producers_state.items():
                if not state.get("is_operational", True):
                    self.agent.any_producer_failed = True
                    break

            R = self.agent.round_id
            if R:
                self.agent.status_seen_round[R].add(sender)
            self.agent._add_event("production", sender, data)
            self.agent.current_solar = data.get("solar_irradiance", self.agent.current_solar)
            self.agent.current_wind = data.get("wind_speed", self.agent.current_wind)
            self.agent.current_temp = data.get("temperature_c", self.agent.current_temp)
            return

        if msg_type == "statusBattery":
            data = json.loads(msg.body)
            self.agent.storage_state[sender] = data
            R = self.agent.round_id
            if R:
                self.agent.status_seen_round[R].add(sender)
            self.agent._add_event("battery_status", sender, data)
            return

        if msg_type == "energy_request":
            data = json.loads(msg.body)
            R = self.agent.round_id
            if data.get("round_id") != R:
                return
            buyer = sender
            need_kwh = float(data.get("need_kwh", 0))
            price_max = float(data.get("price_max", 0))
            self.agent.requests_round[R][buyer] = {
                "need_kwh": need_kwh,
                "price_max": price_max,
            }
            self.agent._add_event("request", buyer, need_kwh, price_max, R)
            return

        if msg_type == "energy_offer":
            data = json.loads(msg.body)
            rid = data.get("round_id")
            seller = sender
            offer = float(data.get("offer_kwh", 0))
            price = float(data.get("price", 0))
            now = time.time()
            R = self.agent.round_id

            if sender in self.agent.producers_state:
                producer_state = self.agent.producers_state[sender]
                if not producer_state.get("is_operational", True):
                    return

            if (
                rid == R
                and self.agent.round_deadline_ts > 0.0
                and now <= self.agent.round_deadline_ts
            ):
                self.agent.offers_round[R][seller] = {
                    "offer_kwh": offer,
                    "price": price,
                    "ts": now,
                }
                self.agent._add_event("offer", seller, offer, price, R)
            else:
                self.agent._add_event("late", seller, offer, price, rid)
            return

        if msg_type == "declined_offer":
            data = json.loads(msg.body)
            rid = data.get("round_id")
            R = self.agent.round_id
            if rid == R:
                self.agent.declined_round[R].add(sender)
                self.agent._add_event("declined", sender, {}, None, R)