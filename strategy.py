"""
strategy.py - Midpoint Anchored Market Making
"""
import math

def kalshi_fee_cents(price_cents: float) -> float:
    p = max(0.0, min(1.0, price_cents / 100.0))
    return math.ceil(7.0 * p * (1.0 - p))


def generate_quotes(orderbook: dict, current_yes_inventory: int, max_inventory: int) -> dict:
    yes = orderbook.get("yes", [])
    no = orderbook.get("no", [])

    if not yes or not no:
        return {"status": "error", "reason": "One-sided book"}

    best_yes_bid = max((p for p, _ in yes), default=0)
    best_no_bid = max((p for p, _ in no), default=0)

    implied_yes_ask = 100 - best_no_bid
    yes_midpoint = (best_yes_bid + implied_yes_ask) / 2.0

    LOW, HIGH = 15.0, 85.0
    if yes_midpoint < LOW or yes_midpoint > HIGH:
        return {"status": "skip", "reason": f"midpoint {yes_midpoint:.0f}¢ out of band"}

    # More aggressive quoting
    base_yes_bid = math.floor(yes_midpoint - 1.0)
    base_no_bid = math.floor((100 - yes_midpoint) - 1.0)

    our_yes_bid = max(1, base_yes_bid)
    our_no_bid = max(1, base_no_bid)

    # Inventory skew
    if current_yes_inventory >= max_inventory:
        our_yes_bid = max(1, our_yes_bid - 3)
    elif current_yes_inventory <= -max_inventory:
        our_no_bid = max(1, our_no_bid - 3)

    return {
        "status": "active",
        "quotes": {
            "yes_bid": our_yes_bid,
            "no_bid": our_no_bid,
        }
    }
