"""The Farshore court's rules, exercised through the real contract against a stub of the runtime, the oracle and the web.

farshore.py is loaded with a fake `genlayer` module. The stub keeps the oracle's claims in memory as the EVM side
would, records every settlement the court sends, serves fixed answers for each of the two sources per network (Horizon
and StellarExpert for Stellar, Blockscout and the chain's own RPC for Base Sepolia), records which URLs were read, and
runs the real validator check against the leader's result.

    python tests/farshore_rules.py
"""

import base64
import hashlib
import io
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
CONTRACT = os.path.join(HERE, "..", "contracts", "farshore.py")

HORIZON = "https://horizon-testnet.stellar.org"
EXPERT = "https://api.stellar.expert/explorer/testnet"
BLOCKSCOUT = "https://base-sepolia.blockscout.com"
BASE_RPC = "https://sepolia.base.org"
USER_PK = bytes.fromhex("c558eb92b30df72fb94b1bf99274a3b266ca769ced75b535fa6780ae7d0d37ef")
USER_G = "GDCVR24SWMG7OL5ZJMN7TETUUOZGNSTWTTWXLNJV7JTYBLT5BU366CLO"          # the same key, from the Stellar SDK
OTHER_PK = bytes.fromhex("5e9dce97737da570d20a359802" + "5a01705ee6d3af06a0215748b0aba686" + "37615d")
EVM_USER = bytes(12) + bytes.fromhex("80519c53f10d731e4ff83a7d9acd69cf98da6258")
# A real transaction from the proof (order A): its envelope and result, as StellarExpert serves them.
REAL_TX = "7d2243085eed42e8482dfd732fe43cd6e27d4c5f8fd61e6d3650dd28d5332db0"
REAL_BODY = "AAAAAgAAAABenc6Xc32lcNIKNZgCWgFwXubTrwagIVdIsKumhjdhXQAAAGQAV94yAAAABQAAAAEAAAAAAAAAAAAAAABq+v+rAAAAAyiBCq97XtJb7ZTLb+ogWy7e4vFe2Pu9xOZqRnKTlFyLAAAAAQAAAAAAAAABAAAAAMVY65KzDfcvuUsb+ZJ0o7Jmynac7XW1NfpngK59DTfvAAAAAAAAAAABfXhAAAAAAAAAAAGGN2FdAAAAQEG4OCny/roZVJl17fPYyCml6e1F6AqShBSkP4lMvRKdF4LkL+yIXL8BiIVtfVeua4Wn8IrEaaa728XicF27gwY="
REAL_RESULT = "AAAAAAAAAGQAAAAAAAAAAQAAAAAAAAABAAAAAAAAAAA="
REAL_MEMO = bytes.fromhex("28810aaf7b5ed25bed94cb6fea205b2edee2f15ed8fbbdc4e66a467293945c8b")


class _Store:
    def __init__(self, kind): self.kind = kind
    def __class_getitem__(cls, item): return cls("map" if isinstance(item, tuple) else "list")
    def make(self): return {} if self.kind == "map" else []


class _Address:
    def __init__(self, h): self.as_hex = str(h.as_hex if isinstance(h, _Address) else h)


class _Return:
    def __init__(self, calldata): self.calldata = calldata


class RoundFailed(Exception):
    pass


def tag(network, asset):
    return network.encode().ljust(16, b"\x00") + asset.encode().ljust(16, b"\x00")


def memo_for(order, solver):
    return hashlib.sha256(order + solver).digest()


class OracleState:
    def __init__(self):
        self.claims = {}
        self.sent = []

    def add(self, key, network="stellar-testnet", asset="XLM", amount=25_000_000, recipient=USER_PK, tx=b"\x11" * 32,
            order=b"\x0a" * 32, solver=bytes(12) + b"\x5e" * 20, claimed_at=1_000_000, status=1):
        self.claims[key] = dict(status=status, order=order, solver=solver, tx=tx, token=tag(network, asset), amount=amount,
                                recipient=recipient, claimed_at=claimed_at)


def make_interface(o):
    def contract_interface(cls):
        class Proxy:
            def __init__(self, address): self.address = address

            def view(self):
                def facts(key):
                    c = o.claims.get(bytes(key))
                    if c is None:
                        return (0, bytes(32), bytes(32), bytes(32), bytes(32), 0, bytes(32), 0)
                    return (c["status"], c["order"], c["solver"], c["tx"], c["token"], c["amount"], c["recipient"], 0)
                return types.SimpleNamespace(
                    claimFacts=facts,
                    claimTimes=lambda key: (o.claims[bytes(key)]["claimed_at"], bytes(32)),
                    memoFor=lambda order, solver: memo_for(bytes(order), bytes(solver)),
                )

            def emit(self, value=0):
                return types.SimpleNamespace(settle=lambda key, filled, at, reason: o.sent.append((bytes(key), bool(filled), int(at), bytes(reason))))
        return Proxy
    return contract_interface


class _Res:
    def __init__(self, status, body): self.status, self.body = status, body


class _Web:
    """GET: URL -> (status, body). POST (JSON-RPC batches): (url, method, first param) -> result."""

    def __init__(self):
        self.pages, self.rpc, self.read = {}, {}, []

    def get(self, url, **kw):
        self.read.append(url)
        if url not in self.pages:
            raise IOError("no route to " + url)
        ans = self.pages[url]
        if isinstance(ans, list):
            ans = ans.pop(0) if len(ans) > 1 else ans[0]
        status, body = ans
        return _Res(status, body if isinstance(body, bytes) else json.dumps(body).encode())

    def post(self, url, body=b"", headers=None):
        self.read.append(url)
        if url not in {u for (u, _, _) in self.rpc}:
            return _Res(503, b"down")
        out = []
        for call in json.loads(body.decode()):
            out.append({"jsonrpc": "2.0", "id": call["id"], "result": self.rpc.get((url, call["method"], str(call["params"][0])))})
        return _Res(200, json.dumps(out).encode())


class _VM:
    Return = _Return

    def run_nondet(self, lead, agree):
        leader = lead()
        if not agree(_Return(leader)):
            raise RoundFailed("validators did not agree")
        return leader


def load(o):
    gl = types.ModuleType("genlayer")
    gl.public = types.SimpleNamespace(write=lambda fn: fn, view=lambda fn: fn)
    gl.message = types.SimpleNamespace(sender_address=_Address("0x" + "1" * 40))
    gl.nondet = types.SimpleNamespace(web=_Web())
    gl.vm = _VM()
    gl.storage = types.SimpleNamespace(TreeMap=_Store, DynArray=_Store)
    gl.contract = types.SimpleNamespace(Contract=object)
    gl.Address = _Address
    gl.u8 = gl.u32 = gl.u256 = int
    gl.evm = types.SimpleNamespace(contract_interface=make_interface(o), bytes32=bytes)
    sys.modules["genlayer"] = gl
    module = types.ModuleType("farshore_under_test")
    exec(compile(io.open(CONTRACT, encoding="utf-8").read(), CONTRACT, "exec"), module.__dict__)
    return module, gl


def fresh(module):
    c = module.Farshore.__new__(module.Farshore)
    for field, declared in module.Farshore.__annotations__.items():
        if isinstance(declared, _Store):
            setattr(c, field, declared.make())
    c.__init__("0x" + "a" * 40)
    return c


RESULTS = []


def check_(label, cond):
    RESULTS.append((label, bool(cond)))
    print(("  ok  " if cond else " FAIL "), label)


# ------------------------------------------------------------------ fixtures: the same payment, as each source shows it

def xdr_envelope(memo, pays, memo_type=3):
    """A TransactionV1Envelope with a hash memo and payment operations: (pubkey, native?, stroops)."""
    u32 = lambda n: n.to_bytes(4, "big")
    i64 = lambda n: n.to_bytes(8, "big", signed=True)
    out = u32(2) + u32(0) + b"\x01" * 32 + u32(100) + i64(5) + u32(0)
    out += u32(memo_type) + (memo if memo_type == 3 else (u32(4) + b"note") if memo_type == 1 else b"")
    out += u32(len(pays))
    for pk, native, n in pays:
        out += u32(0) + u32(1) + u32(0) + pk + (u32(0) if native else u32(1) + b"USDC" + u32(0) + b"\x02" * 32) + i64(n)
    out += u32(0) + u32(0)
    return base64.b64encode(out).decode()


def xdr_result(ok=True):
    return base64.b64encode((100).to_bytes(8, "big") + (0 if ok else -1).to_bytes(4, "big", signed=True) + bytes(8)).decode()


def serve_stellar(web, tx, memo, pays, ok=True, ts=1794819600, expert=None, memo_type="hash"):
    """pays: [(pubkey, native, stroops)]. `expert` overrides what the second source shows."""
    def horizon_amount(n): return str(n // 10_000_000) + "." + str(n % 10_000_000).rjust(7, "0")
    from datetime import datetime, timezone
    t = {"hash": tx, "successful": ok, "memo_type": memo_type, "created_at": datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    if memo_type == "hash":
        t["memo"] = base64.b64encode(memo).decode()
    elif memo_type == "text":
        t["memo"] = "rule FILLED"
    web.pages[HORIZON + "/transactions/" + tx] = (200, t)
    ops = [{"type": "payment", "asset_type": "native" if nat else "credit_alphanum4", "to": MOD.stellar_account(pk), "amount": horizon_amount(n)} for pk, nat, n in pays]
    web.pages[HORIZON + "/transactions/" + tx + "/operations?limit=200"] = (200, {"_embedded": {"records": ops}})
    e_memo, e_pays, e_ok, e_ts = (expert or {}).get("memo", memo), (expert or {}).get("pays", pays), (expert or {}).get("ok", ok), (expert or {}).get("ts", ts)
    web.pages[EXPERT + "/tx/" + tx] = (200, {"hash": tx, "ts": e_ts, "body": xdr_envelope(e_memo, e_pays, 3 if memo_type == "hash" else 1), "result": xdr_result(e_ok)})


def serve_evm(web, tx, memo, to, value, ok=True, ts=1794819600, rpc=None):
    from datetime import datetime, timezone
    web.pages[BLOCKSCOUT + "/api/v2/transactions/0x" + tx] = (200, {"hash": "0x" + tx, "status": "ok", "result": "success" if ok else "Reverted",
        "raw_input": "0x" + memo.hex(), "to": {"hash": to}, "value": str(value), "timestamp": datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000Z")})
    r = rpc or {}
    web.rpc[(BASE_RPC, "eth_getTransactionByHash", "0x" + tx)] = {"to": r.get("to", to), "value": hex(r.get("value", value)), "input": "0x" + r.get("memo", memo).hex()}
    web.rpc[(BASE_RPC, "eth_getTransactionReceipt", "0x" + tx)] = {"status": "0x1" if r.get("ok", ok) else "0x0", "blockNumber": "0x10"}
    web.rpc[(BASE_RPC, "eth_getBlockByNumber", "0x10")] = {"timestamp": hex(r.get("ts", ts))}


MOD = None


def main():
    global MOD
    o = OracleState()
    module, gl = load(o)
    MOD = module
    web = gl.nondet.web
    J = json.loads
    order, solver = b"\x0a" * 32, bytes(12) + b"\x5e" * 20
    memo = memo_for(order, solver)

    print("the encodings the court relies on")
    check_("a raw ed25519 key becomes the same G... account the Stellar SDK gives", module.stellar_account(USER_PK) == USER_G)
    check_("stroops are written and read as Horizon writes amounts", module.stroops(25_000_000) == "2.5000000" and module._stroops_of("2.5000000") == 25_000_000 and module._stroops_of("0.0000001") == 1)
    env = module.stellar_envelope(REAL_BODY)
    check_("a real Stellar envelope decodes to the memo and the payment Horizon shows for it", env["memo"] == REAL_MEMO and env["pays"] == [(USER_G, "XLM", 25_000_000)])
    check_("a real Stellar result decodes as a success", module.stellar_result_ok(REAL_RESULT) and not module.stellar_result_ok(xdr_result(False)))

    print("\ntwo sources, judged alike")
    tx = "aa" * 32

    def st(**kw):
        return module.check("stellar-testnet", tx, kw.get("memo", memo), kw.get("rec", USER_PK), kw.get("amount", 25_000_000))

    serve_stellar(web, tx, memo, [(USER_PK, True, 25_000_000)])
    r = st()
    check_("when both sources show exactly the amount, to the recipient, with the memo, it is FILLED at the transaction's own time", r["verdict"] == "FILLED" and r["ts"] == 1794819600)
    check_("the ruling carries a digest of what was agreed", r["evidence"] == module.evidence("stellar-testnet", tx, "FILLED", 1794819600, memo, USER_PK, 25_000_000))
    check_("the same payment read for another solver or order is NOT_FILLED: the memo binds it", st(memo=b"\x00" * 32)["verdict"] == "NOT_FILLED")
    check_("one stroop short is NOT_FILLED", st(amount=25_000_001)["verdict"] == "NOT_FILLED")
    serve_stellar(web, tx, memo, [(USER_PK, False, 25_000_000)])
    check_("the right amount of another asset is NOT_FILLED", st()["verdict"] == "NOT_FILLED")
    serve_stellar(web, tx, memo, [(OTHER_PK, True, 25_000_000)])
    check_("a payment to someone else is NOT_FILLED", st()["verdict"] == "NOT_FILLED")
    serve_stellar(web, tx, memo, [(USER_PK, True, 25_000_000)], ok=False)
    check_("a failed transaction is NOT_FILLED", st()["verdict"] == "NOT_FILLED")
    serve_stellar(web, tx, memo, [(USER_PK, True, 25_000_000)], memo_type="text")
    check_("a text memo, whatever it says, is NOT_FILLED", st()["verdict"] == "NOT_FILLED")

    print("\nwhen the sources disagree")
    serve_stellar(web, tx, memo, [(USER_PK, True, 25_000_000)], expert={"pays": [(USER_PK, True, 24_000_000)]})
    r = st()
    check_("one source showing the full amount and the other less is SPLIT: no verdict", r["verdict"] == "SPLIT")
    serve_stellar(web, tx, memo, [(USER_PK, True, 25_000_000)], expert={"memo": b"\x00" * 32})
    check_("a memo that differs between sources is SPLIT", st()["verdict"] == "SPLIT")
    serve_stellar(web, tx, memo, [(USER_PK, True, 25_000_000)], expert={"ts": 1794819601})
    check_("a time that differs between sources is SPLIT", st()["verdict"] == "SPLIT")
    serve_stellar(web, tx, memo, [(USER_PK, True, 25_000_000)])
    web.pages[EXPERT + "/tx/" + tx] = (404, {"error": "Not found", "status": 404})
    check_("one source not having the transaction yet is SPLIT, not FILLED", st()["verdict"] == "SPLIT")
    web.pages[HORIZON + "/transactions/" + tx] = (404, {"status": 404})
    check_("neither source having it is MISSING", st()["verdict"] == "MISSING")
    serve_stellar(web, tx, memo, [(USER_PK, True, 25_000_000)])
    web.pages[EXPERT + "/tx/" + tx] = (503, b"<html>down</html>")
    check_("a source that cannot be read gives no verdict", st()["verdict"] == "UNREADABLE")
    web.pages[EXPERT + "/tx/" + tx] = (200, {"hash": tx, "ts": 1, "body": "AAAA", "result": REAL_RESULT})
    check_("an envelope that does not decode gives no verdict", st()["verdict"] == "UNREADABLE")

    print("\nreading a Base Sepolia payment")
    etx = "cc" * 32
    to = "0x80519c53f10d731e4ff83a7d9acd69cf98da6258"

    def evm(**kw):
        return module.check("base-sepolia", etx, kw.get("memo", memo), kw.get("rec", EVM_USER), kw.get("amount", 10**14))

    serve_evm(web, etx, memo, to, 10**14)
    r = evm()
    check_("Blockscout and the chain's own RPC agreeing on the transfer is FILLED", r["verdict"] == "FILLED" and r["ts"] == 1794819600)
    check_("the wrong input data is NOT_FILLED", evm(memo=b"\x01" * 32)["verdict"] == "NOT_FILLED")
    check_("the wrong value is NOT_FILLED", evm(amount=10**14 - 1)["verdict"] == "NOT_FILLED")
    check_("a recipient that is not a 20-byte address is NOT_FILLED", evm(rec=b"\x01" * 32)["verdict"] == "NOT_FILLED")
    serve_evm(web, etx, memo, to, 10**14, rpc={"value": 10**13})
    check_("the RPC showing another value than the explorer is SPLIT", evm()["verdict"] == "SPLIT")
    serve_evm(web, etx, memo, to, 10**14, rpc={"ok": False})
    check_("the RPC showing a reverted receipt is SPLIT", evm()["verdict"] == "SPLIT")
    serve_evm(web, etx, memo, to, 10**14)
    web.rpc = {k: v for k, v in web.rpc.items() if k[0] != BASE_RPC}
    check_("an RPC that is down gives no verdict", evm()["verdict"] == "UNREADABLE")

    print("\nwhat the court rules on")
    c = fresh(module)

    def key(n):
        return bytes([n]) * 32

    def hx(n):
        return "0x" + key(n).hex()

    module._now = lambda: 1_000_000 + 60
    check_("a malformed claim id is refused", not J(c.verify("0x12"))["ok"])
    check_("an id with no open claim is refused", "no open claim" in J(c.verify(hx(1)))["error"])
    o.add(key(2), status=2)
    check_("a claim already settled on the oracle is refused", "no open claim" in J(c.verify(hx(2)))["error"])
    check_("nothing was sent by any refusal", not o.sent)

    print("\nverdicts and what they send")
    tx3 = "33" * 32
    o.add(key(3), tx=bytes.fromhex(tx3))
    serve_stellar(web, tx3, memo, [(USER_PK, True, 25_000_000)])
    web.read = []
    r = J(c.verify(hx(3)))
    ruling = J(c.ruling(hx(3)))
    check_("FILLED is sent to the oracle with the transaction's own timestamp", r["ok"] and r["verdict"] == "FILLED" and o.sent[-1][:3] == (key(3), True, 1794819600))
    check_("the reason commits to the claim, the verdict and the evidence",
           o.sent[-1][3] == hashlib.sha256(("farshore|" + key(3).hex() + "|FILLED|" + ruling["evidence"]).encode()).digest() and ruling["reason"] == o.sent[-1][3].hex())
    check_("the court read both of its sources for the network, and nothing else",
           any(u.startswith(HORIZON) for u in web.read) and any(u.startswith(EXPERT) for u in web.read) and all(u.startswith(HORIZON) or u.startswith(EXPERT) for u in web.read))
    check_("the ruling names both sources and keeps the network, the transaction and the solver",
           ruling["sources"] == [HORIZON, EXPERT] and ruling["network"] == "stellar-testnet" and ruling["tx"] == tx3 and ruling["solver"] == "0x" + "5e" * 20)
    o.claims[key(3)]["status"] = 2
    check_("a claim is ruled once", "already ruled" in J(c.verify(hx(3)))["error"])

    tx4 = "44" * 32
    o.add(key(4), tx=bytes.fromhex(tx4), amount=30_000_000)
    serve_stellar(web, tx4, memo, [(USER_PK, True, 29_000_000)])
    r = J(c.verify(hx(4)))
    check_("NOT_FILLED is sent with no timestamp, so no attestation can be written", r["verdict"] == "NOT_FILLED" and o.sent[-1][:3] == (key(4), False, 0))

    print("\nwaiting, never guessing")
    tx5 = "55" * 32
    o.add(key(5), tx=bytes.fromhex(tx5), claimed_at=1_000_000)
    web.pages[HORIZON + "/transactions/" + tx5] = (404, {"status": 404})
    web.pages[EXPERT + "/tx/" + tx5] = (404, {"error": "Not found", "status": 404})
    n = len(o.sent)
    module._now = lambda: 1_000_000 + 60
    r = J(c.verify(hx(5)))
    check_("inside the grace, a transaction neither source shows yet changes nothing", not r["ok"] and "yet" in r["error"] and len(o.sent) == n and J(c.ruling(hx(5))) == {"exists": False})
    module._now = lambda: 1_000_000 + 15 * 60 + 1
    r = J(c.verify(hx(5)))
    check_("after the grace, it is NOT_FILLED", r["verdict"] == "NOT_FILLED" and o.sent[-1][:2] == (key(5), False))
    tx6 = "66" * 32
    o.add(key(6), tx=bytes.fromhex(tx6))
    serve_stellar(web, tx6, memo, [(USER_PK, True, 25_000_000)], expert={"pays": [(USER_PK, True, 1)]})
    n = len(o.sent)
    r = J(c.verify(hx(6)))
    check_("sources that disagree change nothing, at any time", not r["ok"] and "do not agree" in r["error"] and len(o.sent) == n)
    web.pages[EXPERT + "/tx/" + tx6] = (502, b"bad gateway")
    r = J(c.verify(hx(6)))
    check_("a source that cannot be read changes nothing", not r["ok"] and "could not be read" in r["error"] and len(o.sent) == n)
    o.add(key(7), asset="USDC")
    web.read = []
    r = J(c.verify(hx(7)))
    check_("an asset the court does not check is NOT_FILLED, without reading anything", r["verdict"] == "NOT_FILLED" and not web.read)
    o.add(key(8), network="solana-devnet", asset="SOL")
    web.read = []
    r = J(c.verify(hx(8)))
    check_("a network the court does not read is NOT_FILLED, and no URL is built from the claim", r["verdict"] == "NOT_FILLED" and not web.read)

    tx9 = "99" * 32
    o.add(key(9), tx=bytes.fromhex(tx9))
    serve_stellar(web, tx9, memo, [(USER_PK, True, 25_000_000)])
    h9 = web.pages[HORIZON + "/transactions/" + tx9]
    e9 = web.pages[EXPERT + "/tx/" + tx9]
    late = dict(h9[1]); late["created_at"] = "2026-11-16T09:00:05Z"
    late_e = dict(e9[1]); late_e["ts"] = 1794819605
    web.pages[HORIZON + "/transactions/" + tx9] = [h9, (200, late)]
    web.pages[EXPERT + "/tx/" + tx9] = [e9, (200, late_e)]
    n = len(o.sent)
    try:
        c.verify(hx(9))
        failed = False
    except RoundFailed:
        failed = True
    check_("validators that read a different time than the leader do not agree, and nothing is sent", failed and len(o.sent) == n)

    print("\nresending a ruling")
    o.claims[key(4)]["status"] = 1
    stored = J(c.ruling(hx(4)))
    r = J(c.resend(hx(4)))
    check_("a recorded ruling that has not landed is sent again, unchanged, with no network read",
           r["ok"] and o.sent[-1][:3] == (key(4), False, 0) and o.sent[-1][3].hex() == stored["reason"])
    o.claims[key(4)]["status"] = 3
    check_("a ruling the oracle already holds is not sent again", "already holds" in J(c.resend(hx(4)))["error"])
    check_("a claim the court never ruled on cannot be resent", "has not ruled" in J(c.resend(hx(1)))["error"])

    print("\nreads")
    page = J(c.page(0, 10))
    check_("rulings page newest first", page["total"] == 5 and page["items"][0]["claim"] == hx(8))
    pol = J(c.policy())
    check_("the court publishes its two sources per network, its grace and how to retry",
           pol["grace_seconds"] == 900 and pol["networks"]["stellar-testnet"]["second"] == EXPERT and pol["networks"]["base-sepolia"]["second"] == BASE_RPC and "verify again" in pol["retry"])

    passed = sum(1 for _, ok in RESULTS if ok)
    print("\n%d checks, all through the real Farshore court" % len(RESULTS) if passed == len(RESULTS) else "\n%d of %d checks passed" % (passed, len(RESULTS)))
    sys.exit(0 if passed == len(RESULTS) else 1)


if __name__ == "__main__":
    main()
