"""
cost_filter.py — Trade viability gate: refuse a trade when
    expected_move <= min_ratio × estimated_cost

All costs are expressed in basis points (bps = 0.01 %).
"""


class CostFilter:
    """
    Hyperliquid paper fee schedule (taker 3 bps, maker rebate -0.3 bps).
    Slippage and funding buffer are configurable.

    Typical round-trip cost for a taker-in / taker-out trade:
        3 + 3 + 1.5 (slippage) × 2 + 0.5 (funding) ≈ 9.5 bps
    """

    def __init__(
        self,
        maker_fee_bps: float = -0.3,   # rebate
        taker_fee_bps: float =  3.0,
        slippage_entry_bps: float = 0.75,
        slippage_exit_bps: float  = 0.75,
        funding_buffer_bps: float = 0.5,
        min_ratio: float = 3.0,
    ):
        self.maker_fee_bps     = maker_fee_bps
        self.taker_fee_bps     = taker_fee_bps
        self.slippage_entry    = slippage_entry_bps
        self.slippage_exit     = slippage_exit_bps
        self.funding_buffer    = funding_buffer_bps
        self.min_ratio         = min_ratio

    # ------------------------------------------------------------------

    def round_trip_cost_bps(
        self,
        maker_entry: bool = False,
        maker_exit:  bool = False,
    ) -> float:
        entry = self.maker_fee_bps if maker_entry else self.taker_fee_bps
        exit_ = self.maker_fee_bps if maker_exit  else self.taker_fee_bps
        return (entry + exit_
                + self.slippage_entry + self.slippage_exit
                + self.funding_buffer)

    def is_worth_taking(
        self,
        expected_move_bps: float,
        maker_entry: bool = False,
        maker_exit:  bool = False,
    ) -> tuple[bool, str, float]:
        """
        Returns (ok, reason_str, reward_to_cost_ratio).

        ok=True  → trade is viable.
        ok=False → skip, reason explains why.
        """
        cost  = self.round_trip_cost_bps(maker_entry, maker_exit)
        ratio = expected_move_bps / max(cost, 0.001)
        ok    = ratio >= self.min_ratio
        if ok:
            reason = f"cost_ok ratio={ratio:.1f}x move={expected_move_bps:.1f}bps cost={cost:.1f}bps"
        else:
            reason = (f"cost_too_high ratio={ratio:.1f}x "
                      f"move={expected_move_bps:.1f}bps cost={cost:.1f}bps "
                      f"need {self.min_ratio}x")
        return ok, reason, ratio
