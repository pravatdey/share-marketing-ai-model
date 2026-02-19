"""
Order Manager – places and tracks buy/sell orders via the Upstox v2 REST API.

Order types used:
  - MARKET order (instant fill at best available price)
  - Product type: MIS (Margin Intraday Square-off) for intraday leverage
"""

from __future__ import annotations

import logging
import time
import requests
from typing import Optional

import config.settings as cfg
from src.strategy import Signal

logger = logging.getLogger(__name__)


class OrderManager:
    def __init__(self, access_token: str):
        self._headers = {
            "accept":        "application/json",
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {access_token}",
            "Api-Version":   "2.0",
        }
        self._placed_orders: list[dict] = []  # history of placed orders today

    # ── Order Placement ───────────────────────────────────────────────────────

    def place_order(
        self,
        signal: Signal,
        transaction_type: str,   # "BUY" or "SELL"
    ) -> Optional[str]:
        """
        Place a MARKET MIS order.

        Args:
            signal: Signal object with instrument_key, quantity, ltp.
            transaction_type: "BUY" or "SELL"

        Returns:
            order_id (str) on success, None on failure.
        """
        if signal.quantity < 1:
            logger.warning("Skipping order – quantity is 0.")
            return None

        payload = {
            "quantity":         signal.quantity,
            "product":          "MIS",          # intraday margin order
            "validity":         "DAY",
            "price":            0,              # 0 = market price
            "tag":              "auto_bot",
            "instrument_token": signal.instrument_key,
            "order_type":       "MARKET",
            "transaction_type": transaction_type,
            "disclosed_quantity": 0,
            "trigger_price":    0,
            "is_amo":           False,
        }

        url = f"{cfg.UPSTOX_BASE_URL}/order/place"
        try:
            resp = requests.post(url, json=payload, headers=self._headers, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error("Order placement failed: %s", exc)
            return None

        data     = resp.json()
        order_id = data.get("data", {}).get("order_id")

        record = {
            "order_id":        order_id,
            "instrument_key":  signal.instrument_key,
            "transaction_type": transaction_type,
            "quantity":        signal.quantity,
            "ltp_at_signal":   signal.ltp,
            "reason":          signal.reason,
        }
        self._placed_orders.append(record)

        if order_id:
            logger.info(
                "Order placed → %s | %s | qty=%d | order_id=%s",
                transaction_type, signal.instrument_key, signal.quantity, order_id,
            )
        else:
            logger.error("Order placed but no order_id returned: %s", data)

        return order_id

    # ── Order Status ──────────────────────────────────────────────────────────

    def get_order_status(self, order_id: str) -> dict:
        """Return the full order detail from Upstox."""
        url = f"{cfg.UPSTOX_BASE_URL}/order/details"
        params = {"order_id": order_id}
        try:
            resp = requests.get(url, headers=self._headers, params=params, timeout=10)
            resp.raise_for_status()
            return resp.json().get("data", {})
        except Exception as exc:
            logger.error("Failed to fetch order status for %s: %s", order_id, exc)
            return {}

    def wait_for_fill(self, order_id: str, max_wait: int = 30) -> Optional[float]:
        """
        Poll until order is COMPLETE and return the average fill price.
        Waits up to max_wait seconds.
        """
        for _ in range(max_wait // 2):
            time.sleep(2)
            status = self.get_order_status(order_id)
            if status.get("status") == "complete":
                avg_price = float(status.get("average_price", 0))
                logger.info("Order %s filled @ %.2f", order_id, avg_price)
                return avg_price
            if status.get("status") in ("cancelled", "rejected"):
                logger.warning("Order %s was %s", order_id, status.get("status"))
                return None

        logger.warning("Order %s not filled within %d seconds", order_id, max_wait)
        return None

    # ── Portfolio ─────────────────────────────────────────────────────────────

    def get_positions(self) -> list[dict]:
        """Return current open intraday positions."""
        url = f"{cfg.UPSTOX_BASE_URL}/portfolio/short-term-positions"
        try:
            resp = requests.get(url, headers=self._headers, timeout=10)
            resp.raise_for_status()
            return resp.json().get("data", [])
        except Exception as exc:
            logger.error("Failed to fetch positions: %s", exc)
            return []

    def exit_all_positions(self) -> None:
        """Square off ALL open MIS positions (used at forced exit time)."""
        positions = self.get_positions()
        if not positions:
            logger.info("No open positions to exit.")
            return

        for pos in positions:
            qty       = abs(int(pos.get("quantity", 0)))
            inst_key  = pos.get("instrument_token", "")
            side      = pos.get("quantity", 0)  # positive = long, negative = short

            if qty == 0:
                continue

            transaction_type = "SELL" if side > 0 else "BUY"  # reverse to exit

            dummy_signal = type("S", (), {
                "instrument_key": inst_key,
                "quantity": qty,
                "ltp": pos.get("last_price", 0),
                "reason": "force-exit at market close",
            })()

            self.place_order(dummy_signal, transaction_type)   # type: ignore
            logger.info(
                "Force-exiting %s × %d via %s order",
                inst_key, qty, transaction_type,
            )

    # ── Today's Summary ───────────────────────────────────────────────────────

    def get_today_orders(self) -> list[dict]:
        return self._placed_orders
