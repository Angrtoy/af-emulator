"""Stress test: inventory mutators on the same UIN must not lose updates."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CODE = r"""
import sys, threading
import assaultfire_server_v143b as s
sys.setswitchinterval(1e-6)          # provoke thread interleaving

UIN = 10001
s._v140_select_player(UIN)
currency, price = s._v140_price(210006, 3)
key = {"TP": "ap", "GP": "gp", "MP": "mp"}[currency]
req = {"count": 1, "buy_type": 1, "consignne": UIN,
       "pay_type": s._v140_expected_pay_type(currency),
       "commodities": [{"commodity_id": 210006, "price_index": 3}]}
s._v140_wallet()[key] = price * 10_000
item_id = next(iter(s._v140_build_commodity_props(210006, duration_hours=3,
                                                  obtained_at=0)))["item_id"]

SEED, BUYS = 60, 150
for _ in range(SEED):
    s._v140_commit_purchase(req, self_uin=UIN)
start = len(s.V111_INVENTORY)

consumed, bought = [], []
def consumer():
    s._V140_PLAYER_STATE.select(UIN)
    for _ in range(SEED):
        if s._v160_consume_inventory_item(item_id, "stress") is not None:
            consumed.append(1)
def buyer():
    s._V140_PLAYER_STATE.select(UIN)
    for _ in range(BUYS):
        s._v140_commit_purchase(req, self_uin=UIN); bought.append(1)
ts = [threading.Thread(target=consumer), threading.Thread(target=buyer)]
[t.start() for t in ts]; [t.join() for t in ts]

expected = start + len(bought) - len(consumed)
got = len(s.V111_INVENTORY)
assert got == expected, f"lost update: inventory={got} expected={expected} (bought={len(bought)} consumed={len(consumed)})"
print("OK")
"""


class InventoryRaceTests(unittest.TestCase):
    def test_consume_and_purchase_do_not_lose_updates(self):
        with tempfile.TemporaryDirectory() as td:
            env = os.environ.copy()
            env.update({"AF_ACCOUNT_DB": str(Path(td) / "a.sqlite3"),
                        "AF_RUNTIME_MODE": "production", "AF_DEV_WEB": "0",
                        "PYTHONPATH": str(ROOT / "server")})
            r = subprocess.run([sys.executable, "-c", CODE], cwd=ROOT / "server",
                               env=env, capture_output=True, text=True, timeout=240)
            self.assertEqual(r.returncode, 0, r.stdout[-1200:] + r.stderr[-1500:])
            self.assertIn("OK", r.stdout)


if __name__ == "__main__":
    unittest.main()
