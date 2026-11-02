"""List-price estimates. The price table is a JSON file the owner can edit."""
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_cache = {}


def load(path=None):
    path = path or os.path.join(_HERE, "prices.json")
    if path not in _cache:
        with open(path, encoding="utf-8") as f:
            _cache[path] = json.load(f)
    return _cache[path]


def cost_usd(prices, model, speed, inp, w5, w1, read, out):
    """Estimated cost in USD, or None when the model has no price entry."""
    model = model or ""
    keys = sorted(set(prices["models"]) | set(prices.get("tiered", {})), key=len, reverse=True)
    key = next((k for k in keys if k in model), None)
    if key is None:
        return None
    if key in prices.get("tiered", {}):
        t = prices["tiered"][key]
        pr = t["high"] if inp + w5 + w1 + read > t["threshold_prompt_tokens"] else t["low"]
    else:
        pr = prices["models"][key]
    pin, p5, p1, prd, pout = pr["in"], pr["w5m"], pr["w1h"], pr["read"], pr["out"]
    fast = prices.get("fast", {}).get(key)
    if speed == "fast" and fast:
        pin, pout = fast["in"], fast["out"]
        p5, p1, prd = pin * fast["w5m_mult"], pin * fast["w1h_mult"], pin * fast["read_mult"]
    return (inp * pin + w5 * p5 + w1 * p1 + read * prd + out * pout) / 1e6
