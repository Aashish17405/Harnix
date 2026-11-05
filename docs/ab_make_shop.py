"""Create the A/B repository: a ~60-file 'shop' package with a bug at the end of a 4-module call chain.

python make_shop.py DEST
"""
import random
import sys
from pathlib import Path

dest = Path(sys.argv[1])
random.seed(11)
files: dict[str, str] = {}

files["shop/__init__.py"] = ""
files["shop/money.py"] = '''"""Money helpers shared across the shop."""
import math


def round_money(value):
    """Round a price to cents (half up)."""
    return math.floor(value * 100) / 100


def format_money(value):
    return f"${value:,.2f}"


def to_cents(value):
    return int(round(value * 100))
'''
files["shop/pricing/__init__.py"] = ""
files["shop/pricing/discounts.py"] = '''"""Discount codes."""
from shop.money import to_cents

CODES = {"SAVE15": 0.15, "SAVE10": 0.10, "WELCOME5": 0.05}


def discount_rate(code):
    return CODES.get((code or "").upper(), 0.0)


def apply_discount(subtotal, code):
    """Price after the discount code, not yet rounded."""
    return subtotal * (1 - discount_rate(code))


def savings_cents(subtotal, code):
    return to_cents(subtotal) - to_cents(apply_discount(subtotal, code))
'''
files["shop/pricing/totals.py"] = '''"""Cart totals."""
from shop.money import round_money
from shop.pricing.discounts import apply_discount


def line_total(unit_price, quantity):
    return unit_price * quantity


def price_cart(cart, code=None):
    subtotal = sum(line_total(i["price"], i["qty"]) for i in cart)
    return round_money(apply_discount(subtotal, code))
'''
files["shop/checkout.py"] = '''"""Checkout orchestration."""
from shop.pricing.totals import price_cart
from shop.shipping.rates import shipping_for


def checkout(cart, code=None, country="US"):
    items = price_cart(cart, code)
    return {"items": items, "shipping": shipping_for(country, items), "currency": "USD"}
'''
files["tests/test_checkout.py"] = '''from shop.checkout import checkout


def test_plain_total():
    assert checkout([{"price": 10.0, "qty": 2}])["items"] == 20.0


def test_discounted_total():
    cart = [{"price": 12.5, "qty": 3}]
    assert checkout(cart, code="SAVE15")["items"] == 31.88
'''

# distractors: many modules with similar vocabulary, none of them involved in the bug
areas = {
    "shipping": ["rates", "zones", "carriers", "labels", "tracking", "customs"],
    "inventory": ["stock", "warehouse", "reorder", "audit", "locations", "batches"],
    "users": ["accounts", "profiles", "sessions", "permissions", "preferences", "history"],
    "catalog": ["products", "categories", "search", "images", "reviews", "variants"],
    "payments": ["gateway", "refunds", "invoices", "receipts", "fraud", "currency"],
    "reports": ["daily", "monthly", "exports", "charts", "forecast", "audit"],
}
for area, mods in areas.items():
    files[f"shop/{area}/__init__.py"] = ""
    for m in mods:
        funcs = []
        for k in range(3):
            funcs.append(f"def {m}_op{k}(x):\n    '''{area} {m} operation {k}.'''\n    return x + {k}\n")
        extra = ""
        if area == "shipping" and m == "rates":
            files["shop/shipping/rates.py"] = (
                '"""Shipping rates."""\n\nRATES = {"US": 4.99, "CA": 7.99, "EU": 9.99}\n\n\n'
                'def round_rate(value):\n    return round(value, 2)\n\n\n'
                'def shipping_for(country, items_total):\n    base = RATES.get(country, 12.99)\n'
                '    return 0.0 if items_total >= 100 else round_rate(base)\n'
            )
            continue
        if area in ("payments", "reports", "catalog") and random.random() < 0.7:
            extra = "from shop.money import format_money\n\n\n"
            funcs.append(f"def {m}_label(x):\n    return format_money(x)\n")
        files[f"shop/{area}/{m}.py"] = f'"""{area} / {m}."""\n{extra}' + "\n\n".join(funcs)
    files[f"tests/test_{area}.py"] = (
        f"from shop.{area}.{mods[1]} import {mods[1]}_op0\n\n\ndef test_{area}_basic():\n    assert {mods[1]}_op0(1) == 1\n"
    )

for rel, text in files.items():
    p = dest / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")
(dest / "AGENTS.md").write_text("Run the tests with: python -m pytest -q\n", encoding="utf-8", newline="\n")
print(f"created {len(files) + 1} files in {dest}")
