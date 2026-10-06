"""Regression tests: purchases roll back on save failure and cannot double-spend."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CODE = r"""
import threading
import assaultfire_server_v143b as s

UIN = 10001
s._v140_select_player(UIN)
currency, price = s._v140_price(210006, 3)
pay = s._v140_expected_pay_type(currency)
key = {"TP": "ap", "GP": "gp", "MP": "mp"}[currency]
req = {"count": 1, "buy_type": 1, "consignne": UIN, "pay_type": pay,
       "commodities": [{"commodity_id": 210006, "price_index": 3}]}

def fresh(balance):
    s._v140_wallet()[key] = balance

# 1) save failure -> wallet and inventory are rolled back
fresh(price * 5)
inv_before = len(s.V111_INVENTORY)
real_save = s._v140_save_state
def boom(*a, **k): raise OSError("disk full")
s._v140_save_state = boom
try:
    s._v140_commit_purchase(req, self_uin=UIN)
    raise SystemExit("expected rejection")
except s._v140_ShopReject:
    pass
finally:
    s._v140_save_state = real_save
assert s._v140_wallet()[key] == price * 5, "wallet not rolled back"
assert len(s.V111_INVENTORY) == inv_before, "inventory not rolled back"

# 2) concurrent buyers with money for exactly one purchase -> exactly one wins
fresh(price)
ok, rejected = [], []
def buy():
    s._V140_PLAYER_STATE.select(UIN)
    try:
        s._v140_commit_purchase(req, self_uin=UIN); ok.append(1)
    except s._v140_ShopReject:
        rejected.append(1)
ts = [threading.Thread(target=buy) for _ in range(8)]
[t.start() for t in ts]; [t.join() for t in ts]
assert len(ok) == 1, (len(ok), len(rejected))
assert s._v140_wallet()[key] == 0, s._v140_wallet()[key]
print("OK")
"""


class PurchaseAtomicityTests(unittest.TestCase):
    def test_rollback_and_no_double_spend(self):
        with tempfile.TemporaryDirectory() as td:
            env = os.environ.copy()
            env.update({"AF_ACCOUNT_DB": str(Path(td) / "a.sqlite3"),
                        "AF_RUNTIME_MODE": "production", "AF_DEV_WEB": "0",
                        "PYTHONPATH": str(ROOT / "server")})
            r = subprocess.run([sys.executable, "-c", CODE], cwd=ROOT / "server",
                               env=env, capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout[-1500:] + r.stderr[-1500:])
            self.assertIn("OK", r.stdout)


if __name__ == "__main__":
    unittest.main()
