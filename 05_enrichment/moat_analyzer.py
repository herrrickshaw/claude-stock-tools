#!/usr/bin/env python3
"""
moat_analyzer.py
================
Analyzes the strength and durability of competitive moats (competitive advantages)
for stocks identified as Buffett picks.

Moat types:
  1. Brand loyalty — Pricing power due to brand preference (premium to peers)
  2. Switching costs — Expensive or painful to switch providers
  3. Network effects — Value increases with user count
  4. Cost advantage — Lowest-cost producer undercuts competition
  5. Regulatory/Legal — Protected by law or licenses
  6. Proprietary data/IP — Defensible through patents, scale, or data assets

Indicators:
  - Margin stability (pricing power resists competition)
  - Market share persistence (customers don't defect)
  - Price history (ability to raise prices without volume collapse)
  - Return on equity consistency (moat prevents margin erosion)
  - Revenue per employee (indicates efficiency/scale advantage)
"""

from __future__ import annotations
import logging
from dataclasses import dataclass

import pandas as pd
import yfinance as yf

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)


@dataclass
class MoatIndicator:
    """Moat strength indicator: metric, value, strength (1-5), evidence."""
    metric: str
    value: float
    strength: int  # 1=weak, 5=very strong
    signal: str  # "BUY", "HOLD", "AVOID"
    explanation: str


class MoatAnalyzer:
    """Analyzes competitive moat strength for a stock."""

    def __init__(self, ticker: str):
        self.ticker = ticker
        self.t = yf.Ticker(ticker)
        self.info = self.t.info
        self.fi = self.t.income_stmt
        self.bs = self.t.balance_sheet
        self.indicators = []

    def analyze_margin_stability(self) -> MoatIndicator:
        """
        Moat strength: High gross margins sustained over time.
        Signal: If margins erode, competitors are winning.
        """
        if self.fi is None or len(self.fi) < 3:
            return MoatIndicator("margin_stability", 0, 1, "UNKNOWN", "Insufficient data")

        margins = []
        for i in range(min(5, len(self.fi))):
            rev = self.fi.iloc[i].get("Total Revenue", 0)
            cogs = self.fi.iloc[i].get("Cost of Revenue", 0)
            if rev > 0:
                margins.append((rev - cogs) / rev)

        if not margins:
            return MoatIndicator("margin_stability", 0, 1, "UNKNOWN", "No revenue data")

        # Trend: is the lowest margin within 85% of the highest?
        margin_stability = min(margins) / max(margins) if max(margins) > 0 else 0
        strength = min(5, int(margin_stability * 5))  # 0.85 → strength 4, 1.0 → strength 5

        if margin_stability > 0.95:
            signal = "BUY"
            explanation = "Margins rock-solid. Pricing power intact; moat is strong."
        elif margin_stability > 0.85:
            signal = "HOLD"
            explanation = "Margins stable. Moat present but facing pressure."
        else:
            signal = "AVOID"
            explanation = "Margin erosion. Moat is eroding; competition winning."

        return MoatIndicator("margin_stability", margin_stability, strength, signal, explanation)

    def analyze_roe_consistency(self) -> MoatIndicator:
        """
        Moat strength: High ROE sustained across business cycles.
        Signal: Consistent high ROE = moat preventing capital from eroding value.
        """
        if self.fi is None or self.bs is None or len(self.fi) < 3:
            return MoatIndicator("roe_consistency", 0, 1, "UNKNOWN", "Insufficient data")

        roes = []
        for i in range(min(5, len(self.fi))):
            ni = self.fi.iloc[i].get("Net Income", 0)
            eq = self.bs.iloc[i].get("Total Equity", 1) if len(self.bs) > i else 1
            if eq > 0:
                roes.append(ni / eq)

        if len(roes) < 2:
            return MoatIndicator("roe_consistency", 0, 1, "UNKNOWN", "Insufficient history")

        # Consistency: all ROEs > 15%? Standard deviation low?
        avg_roe = sum(roes) / len(roes)
        min_roe = min(roes)
        consistency = min_roe / avg_roe if avg_roe > 0 else 0

        strength = min(5, int(avg_roe * 33))  # 0.15 (15%) → strength 5

        if avg_roe > 0.20 and consistency > 0.9:
            signal = "BUY"
            explanation = "Consistently high ROE (>20%). Elite capital allocator; strong moat."
        elif avg_roe > 0.15 and consistency > 0.80:
            signal = "HOLD"
            explanation = "Solid ROE (15-20%). Moat present; good capital allocator."
        else:
            signal = "AVOID"
            explanation = "Low/inconsistent ROE. Weak moat; capital not generating returns."

        return MoatIndicator("roe_consistency", avg_roe, strength, signal, explanation)

    def analyze_market_dominance(self) -> MoatIndicator:
        """
        Moat strength: Market share persistence, brand recognition, revenue scale.
        Signal: Larger players have better moats (network effects, switching costs).
        """
        market_cap = self.info.get("marketCap", 0)
        revenue = self.fi.iloc[0].get("Total Revenue", 0) if self.fi is not None and len(self.fi) > 0 else 0

        if market_cap == 0 or revenue == 0:
            return MoatIndicator("market_dominance", 0, 1, "UNKNOWN", "Insufficient data")

        # Price/Sales multiple: lower = more dominant
        ps = market_cap / revenue if revenue > 0 else 0

        # Market dominance heuristic: companies with PS < 5 and high margins are often duopolies/oligopolies
        if ps < 3:
            strength = 5
            signal = "BUY"
            explanation = "Low Price/Sales. Market leader with pricing power and moat."
        elif ps < 5:
            strength = 4
            signal = "HOLD"
            explanation = "Moderate Price/Sales. Established player; moat likely."
        elif ps < 10:
            strength = 2
            signal = "HOLD"
            explanation = "High Price/Sales relative to revenue. Moat less obvious."
        else:
            strength = 1
            signal = "AVOID"
            explanation = "Very high Price/Sales. Commodity player or overvalued."

        return MoatIndicator("market_dominance", ps, strength, signal, explanation)

    def analyze_asset_efficiency(self) -> MoatIndicator:
        """
        Moat strength: Revenue per dollar of assets (asset turnover).
        Signal: High efficiency = less capex needed to grow = moat.
        """
        if self.fi is None or self.bs is None or len(self.fi) == 0:
            return MoatIndicator("asset_efficiency", 0, 1, "UNKNOWN", "Insufficient data")

        revenue = self.fi.iloc[0].get("Total Revenue", 0)
        assets = self.bs.iloc[0].get("Total Assets", 1) if len(self.bs) > 0 else 1

        asset_turnover = revenue / assets if assets > 0 else 0

        # High turnover (>1.5) = asset-light model = moat
        if asset_turnover > 2.0:
            strength = 5
            signal = "BUY"
            explanation = "Asset-light model. Low capex, high returns; strong moat."
        elif asset_turnover > 1.5:
            strength = 4
            signal = "HOLD"
            explanation = "Efficient asset use. Good moat."
        elif asset_turnover > 1.0:
            strength = 2
            signal = "HOLD"
            explanation = "Moderate efficiency. Capital-intensive business."
        else:
            strength = 1
            signal = "AVOID"
            explanation = "Asset-heavy model. Capital-intensive; moat weak."

        return MoatIndicator("asset_efficiency", asset_turnover, strength, signal, explanation)

    def analyze_dividend_policy(self) -> MoatIndicator:
        """
        Moat strength: Dividend growth signals confidence in moat durability.
        Signal: Increasing dividend = management confident in future cash flows.
        """
        div_yield = self.info.get("dividendYield", 0)
        payout_ratio = self.info.get("payoutRatio", 0)

        if div_yield == 0:
            strength = 2
            signal = "NEUTRAL"
            explanation = "No dividend. Growth-focused; moat not yet monetized."
        elif payout_ratio > 0.7:
            strength = 1
            signal = "CAUTION"
            explanation = "High payout ratio. Limited reinvestment for growth."
        elif div_yield > 0.03 and payout_ratio < 0.6:
            strength = 4
            signal = "BUY"
            explanation = "Sustainable dividend with room for growth. Confident management."
        else:
            strength = 3
            signal = "HOLD"
            explanation = "Moderate dividend. Balanced growth and shareholder returns."

        return MoatIndicator("dividend_policy", div_yield, strength, signal, explanation)

    def run_analysis(self) -> dict:
        """Run full moat analysis and return summary."""
        indicators = [
            self.analyze_margin_stability(),
            self.analyze_roe_consistency(),
            self.analyze_market_dominance(),
            self.analyze_asset_efficiency(),
            self.analyze_dividend_policy(),
        ]

        # Moat rating: average strength
        strengths = [i.strength for i in indicators if i.strength > 0]
        moat_rating = sum(strengths) / len(strengths) if strengths else 0

        # Moat signal: majority vote
        signals = [i.signal for i in indicators]
        buy_count = signals.count("BUY")
        avoid_count = signals.count("AVOID")

        if buy_count >= 3:
            overall_signal = "STRONG MOAT"
        elif avoid_count >= 2:
            overall_signal = "WEAK MOAT"
        else:
            overall_signal = "MODERATE MOAT"

        return {
            "ticker": self.ticker,
            "moat_rating": round(moat_rating, 2),
            "overall_signal": overall_signal,
            "indicators": indicators,
        }

    def print_analysis(self):
        """Print analysis to console."""
        analysis = self.run_analysis()

        print(f"\n{'='*70}")
        print(f"MOAT ANALYSIS: {analysis['ticker']}")
        print(f"{'='*70}")
        print(f"Moat Strength: {analysis['moat_rating']}/5.0 ({analysis['overall_signal']})\n")

        for i in analysis["indicators"]:
            star = "★" * i.strength + "☆" * (5 - i.strength)
            print(f"{i.metric.replace('_', ' ').upper():25} {star}  {i.signal:8} {i.value:.2f}")
            print(f"  → {i.explanation}\n")


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python moat_analyzer.py TICKER")
        print("Example: python moat_analyzer.py MSFT")
        sys.exit(1)

    ticker = sys.argv[1]
    analyzer = MoatAnalyzer(ticker)
    analyzer.print_analysis()
