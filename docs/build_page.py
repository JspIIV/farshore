"""Builds docs/index.html, docs/rpc.mjs and docs/oif.mjs for GitHub Pages.

    python docs/build_page.py

The page carries the deployed addresses and the proof's results (results/proof.json) and reads the chains directly; it
needs no server. rpc.mjs and oif.mjs are the same helpers the scripts use, with their one import pointed at a browser
build of ethers.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
read = lambda *p: open(os.path.join(*p), encoding="utf-8").read()
dep = json.loads(read(ROOT, "deployment.json"))
proof = json.loads(read(ROOT, "results", "proof.json"))

keep = {"oracle": dep["oracle"]["address"], "input_settler": dep["input_settler"]["address"], "test_usd": dep["test_usd"]["address"], "court": dep["court"]["address"]}
orders = {}
for tag, o in proof["orders"].items():
    orders[tag] = {"orderId": o["orderId"], "order": o["order"], "out": o["order"]["outputs"][0], "far_tx": o.get("far_tx"),
                   "claim": (o.get("claim") or {}).get("id")}
page_proof = {"passed": proof["passed"], "total": proof["total"], "accounts": proof["accounts"], "orders": orders,
              "checks": [{"name": c["name"], "ok": c["ok"], "note": c.get("note", ""), "tx": c.get("tx"), "gl": bool(c.get("gl"))} for c in proof["checks"]]}

script = read(HERE, "page_script.html").replace("__DEP__", json.dumps(keep)).replace("__PROOF__", json.dumps(page_proof))
head = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Farshore: an intents oracle for chains no bridge reaches</title>
<meta name="description" content="An open source GenLayer testnet demo: Internet Court as an Open Intents Framework oracle that reads Stellar and Base Sepolia and releases the solver's pay on OIF's own settler." />
<link rel="icon" href="logo.svg" />
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500;600&display=swap" rel="stylesheet" />
<style>
""" + read(HERE, "design.css") + """
</style>
</head>
"""
open(os.path.join(HERE, "index.html"), "w", encoding="utf-8", newline="\n").write(head + read(HERE, "page_body.html") + script)
for name in ("rpc", "oif"):
    src = read(ROOT, "lib", name + ".mjs").replace("from 'ethers';", "from 'https://esm.sh/ethers@6.13.4';")
    open(os.path.join(HERE, name + ".js"), "w", encoding="utf-8", newline="\n").write(src)
print("docs/index.html, rpc.js and oif.js written")
