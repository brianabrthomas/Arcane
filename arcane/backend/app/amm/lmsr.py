"""
Logarithmic Market Scoring Rule (LMSR) — Hanson's automated market maker.

Binary market over outcomes {YES, NO} with share inventories q_yes, q_no and a
liquidity parameter b (larger b = deeper book, less price impact per trade).

  Cost function:   C(q) = b * ln( e^(q_yes/b) + e^(q_no/b) )
  Instantaneous price (implied probability):
                   p_yes = e^(q_yes/b) / (e^(q_yes/b) + e^(q_no/b))
  Cost to buy Δ shares of a side:  C(q_after) - C(q_before)   (always > 0)
  Proceeds to sell Δ shares:       C(q_before) - C(q_after)

The maximum the AMM can ever lose is b * ln(2) (bounded subsidy), which is why
b also defines the platform's max risk per market.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class Quote:
    side: str
    shares: float
    cost: float          # USDC in (buy) or out (sell, negative cost)
    avg_price: float
    price_before: float
    price_after: float


def _cost(q_yes: float, q_no: float, b: float) -> float:
    # numerically stable log-sum-exp
    m = max(q_yes, q_no) / b
    return b * (m + math.log(math.exp(q_yes / b - m) + math.exp(q_no / b - m)))


def price_yes(q_yes: float, q_no: float, b: float) -> float:
    d = (q_no - q_yes) / b
    return 1.0 / (1.0 + math.exp(d))


def prices(q_yes: float, q_no: float, b: float) -> tuple[float, float]:
    p = price_yes(q_yes, q_no, b)
    return p, 1.0 - p


def quote_buy(q_yes: float, q_no: float, b: float, side: str, shares: float) -> Quote:
    """Cost (USDC) to buy `shares` of YES or NO."""
    side = side.upper()
    before = _cost(q_yes, q_no, b)
    p_before = price_yes(q_yes, q_no, b)
    if side == "YES":
        after = _cost(q_yes + shares, q_no, b)
        p_after = price_yes(q_yes + shares, q_no, b)
    else:
        after = _cost(q_yes, q_no + shares, b)
        p_after = 1.0 - price_yes(q_yes, q_no + shares, b)
        p_before = 1.0 - p_before
    cost = after - before
    return Quote(side, shares, cost, cost / shares if shares else 0.0, p_before, p_after)


def shares_for_budget(q_yes: float, q_no: float, b: float, side: str, budget: float) -> float:
    """Invert the cost function: how many shares does `budget` USDC buy?"""
    side = side.upper()
    before = _cost(q_yes, q_no, b)
    target = before + budget
    # closed form for binary LMSR
    other = q_no if side == "YES" else q_yes
    this = q_yes if side == "YES" else q_no
    # solve b*ln(e^(this'/b)+e^(other/b)) = target  for this'
    val = math.exp(target / b) - math.exp(other / b)
    if val <= 0:
        return 0.0
    new_this = b * math.log(val)
    return max(0.0, new_this - this)
