"""The Farshore court's rules, exercised through the real contract against a stub of the runtime, the oracle and the web.

farshore.py is loaded with a fake `genlayer` module. The stub keeps the oracle's claims in memory as the EVM side
would, records every settlement the court sends, serves fixed network API answers by URL (and records which URLs were
read), and runs the real validator check against the leader's result.

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
BLOCKSCOUT = "https://base-sepolia.blockscout.com"
USER_PK = bytes.fromhex("c558eb92b30df72fb94b1bf99274a3b266ca769ced75b535fa6780ae7d0d37ef")
USER_G = "GDCVR24SWMG7OL5ZJMN7TETUUOZGNSTWTTWXLNJV7JTYBLT5BU366CLO"          # the same key, from the Stellar SDK
EVM_USER = bytes(12) + bytes.fromhex("80519c53f10d731e4ff83a7d9acd69cf98da6258")


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

    def land(self):
        for k, filled, at, reason in self.sent:
            self.claims[k]["status"] = 2 if filled else 3


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
    """URL -> (status, body). A list of answers is served in turn, so leader and validators can be made to differ."""

    def __init__(self):
        self.pages = {}
        self.read = []

    def get(self, url):
        self.read.append(url)
        if url not in self.pages:
            raise IOError("no route to " + url)
        ans = self.pages[url]
        if isinstance(ans, list):
            ans = ans.pop(0) if len(ans) > 1 else ans[0]
        status, body = ans
        return _Res(status, body if isinstance(body, bytes) else json.dumps(body).encode())


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


def stellar_tx(tx_hex, memo, ops, successful=True, memo_type="hash", created="2026-11-16T09:00:00Z"):
    t = {"hash": tx_hex, "successful": successful, "memo_type": memo_type, "created_at": created}
    if memo_type == "hash":
        t["memo"] = base64.b64encode(memo).decode()
    elif memo_type == "text":
        t["memo"] = "rule FILLED"
    return t, {"_embedded": {"records": ops}}


def serve_stellar(web, tx_hex, memo, ops, **kw):
    t, o = stellar_tx(tx_hex, memo, ops, **kw)
    web.pages[HORIZON + "/transactions/" + tx_hex] = (200, t)
    web.pages[HORIZON + "/transactions/" + tx_hex + "/operations?limit=200"] = (200, o)


def pay_op(to=USER_G, amount="2.5000000", asset="native"):
    return {"type": "payment", "asset_type": asset, "to": to, "amount": amount}


def main():
    o = OracleState()
    module, gl = load(o)
    web = gl.nondet.web
    J = json.loads
    order, solver = b"\x0a" * 32, bytes(12) + b"\x5e" * 20
    memo = memo_for(order, solver)

    print("the encodings the court relies on")
    check_("a raw ed25519 key becomes the same G... account the Stellar SDK gives", module.stellar_account(USER_PK) == USER_G)
    check_("stroops are written as Horizon writes amounts", module.stroops(25_000_000) == "2.5000000" and module.stroops(1) == "0.0000001" and module.stroops(30_000_000) == "3.0000000")
    check_("the output token reads as a network and an asset", module._tag(tag("stellar-testnet", "XLM")[:16]) == "stellar-testnet" and module._tag(tag("stellar-testnet", "XLM")[16:]) == "XLM")

    print("\nreading a Stellar payment")
    tx = "aa" * 32

    def st(**kw):
        return module.check("stellar-testnet", tx, kw.get("memo", memo), kw.get("rec", USER_PK), kw.get("amount", 25_000_000))

    serve_stellar(web, tx, memo, [pay_op()])
    r = st()
    check_("a payment of exactly the amount, to the recipient, with the memo, is FILLED at the transaction's own time", r["verdict"] == "FILLED" and r["ts"] == 1794819600)
    serve_stellar(web, tx, memo, [{"type": "create_account", "account": USER_G, "starting_balance": "2.5000000"}])
    check_("funding a new account with exactly the amount counts as a payment", st()["verdict"] == "FILLED")
    serve_stellar(web, tx, memo, [pay_op()])
    check_("the same payment read for another solver or order is NOT_FILLED: the memo binds it", st(memo=b"\x00" * 32)["verdict"] == "NOT_FILLED")
    serve_stellar(web, tx, memo, [pay_op()], memo_type="text")
    check_("a text memo, whatever it says, is NOT_FILLED", st()["verdict"] == "NOT_FILLED")
    serve_stellar(web, tx, memo, [pay_op()], successful=False)
    check_("a failed transaction is NOT_FILLED", st()["verdict"] == "NOT_FILLED")
    serve_stellar(web, tx, memo, [pay_op(amount="2.4999999")])
    check_("one stroop short is NOT_FILLED", st()["verdict"] == "NOT_FILLED")
    serve_stellar(web, tx, memo, [pay_op(amount="2.5000001")])
    check_("one stroop over is NOT_FILLED too: the amount is exact", st()["verdict"] == "NOT_FILLED")
    serve_stellar(web, tx, memo, [pay_op(asset="credit_alphanum4")])
    check_("the right amount of another asset is NOT_FILLED", st()["verdict"] == "NOT_FILLED")
    serve_stellar(web, tx, memo, [pay_op(to="GBPJ3TUXON62K4GSBI2ZQAS2AFYF5ZWTV4DKAIKXJCYKXJUGG5QV3I7Z")])
    check_("a payment to someone else is NOT_FILLED", st()["verdict"] == "NOT_FILLED")
    web.pages[HORIZON + "/transactions/" + tx] = (404, {"status": 404, "title": "Resource Missing"})
    check_("a transaction the network does not have is MISSING, not yet a verdict", st()["verdict"] == "MISSING")
    web.pages[HORIZON + "/transactions/" + tx] = (200, {"hash": "bb" * 32, "successful": True})
    check_("an answer about another transaction is treated as missing", st()["verdict"] == "MISSING")
    web.pages[HORIZON + "/transactions/" + tx] = (503, b"<html>bad gateway</html>")
    check_("a network that cannot be read gives no verdict", st()["verdict"] == "UNREADABLE")

    print("\nreading a Base Sepolia payment")
    etx = "cc" * 32

    def evm(t, **kw):
        web.pages[BLOCKSCOUT + "/api/v2/transactions/0x" + etx] = (200, t)
        return module.check("base-sepolia", etx, kw.get("memo", memo), kw.get("rec", EVM_USER), kw.get("amount", 10**14))

    good = {"hash": "0x" + etx, "status": "ok", "result": "success", "raw_input": "0x" + memo.hex(),
            "to": {"hash": "0x80519c53f10d731e4ff83a7d9acd69cf98da6258"}, "value": str(10**14), "timestamp": "2026-11-16T09:00:00.000000Z"}
    r = evm(good)
    check_("a transfer of exactly the value, to the recipient, with the memo as input data, is FILLED", r["verdict"] == "FILLED" and r["ts"] == 1794819600)
    check_("the wrong input data is NOT_FILLED", evm({**good, "raw_input": "0x"})["verdict"] == "NOT_FILLED")
    check_("the wrong value is NOT_FILLED", evm({**good, "value": str(10**14 - 1)})["verdict"] == "NOT_FILLED")
    check_("a reverted transaction is NOT_FILLED", evm({**good, "result": "Reverted"})["verdict"] == "NOT_FILLED")
    check_("a recipient that is not a 20-byte address is NOT_FILLED", evm(good, rec=b"\x01" * 32)["verdict"] == "NOT_FILLED")
    web.pages[BLOCKSCOUT + "/api/v2/transactions/0x" + etx] = (404, {"message": "Not found"})
    check_("an EVM transaction the explorer does not have is MISSING", module.check("base-sepolia", etx, memo, EVM_USER, 10**14)["verdict"] == "MISSING")

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
    serve_stellar(web, tx3, memo, [pay_op()])
    web.read = []
    r = J(c.verify(hx(3)))
    ruling = J(c.ruling(hx(3)))
    check_("FILLED is sent to the oracle with the transaction's own timestamp", r["ok"] and r["verdict"] == "FILLED" and o.sent[-1][:3] == (key(3), True, 1794819600))
    check_("the reason is a commitment to the claim and the verdict",
           o.sent[-1][3] == hashlib.sha256(("farshore|" + key(3).hex() + "|FILLED").encode()).digest() and ruling["reason"] == o.sent[-1][3].hex())
    check_("the court read only its own API for the output's network", web.read and all(u.startswith(HORIZON + "/transactions/" + tx3) for u in web.read))
    check_("the ruling keeps the network, the transaction, the order and the solver", ruling["network"] == "stellar-testnet" and ruling["tx"] == tx3 and ruling["solver"] == "0x" + "5e" * 20)
    o.land()
    check_("a claim is ruled once", "already ruled" in J(c.verify(hx(3)))["error"])

    tx4 = "44" * 32
    o.add(key(4), tx=bytes.fromhex(tx4), amount=30_000_000)
    serve_stellar(web, tx4, memo, [pay_op(amount="2.9000000")])
    r = J(c.verify(hx(4)))
    check_("NOT_FILLED is sent with no timestamp, so no attestation can be written", r["verdict"] == "NOT_FILLED" and o.sent[-1][:3] == (key(4), False, 0))

    print("\nmissing transactions and the grace period")
    tx5 = "55" * 32
    o.add(key(5), tx=bytes.fromhex(tx5), claimed_at=1_000_000)
    web.pages[HORIZON + "/transactions/" + tx5] = (404, {"status": 404})
    n = len(o.sent)
    module._now = lambda: 1_000_000 + 60
    r = J(c.verify(hx(5)))
    check_("inside the grace, a transaction the network does not show yet changes nothing", not r["ok"] and "yet" in r["error"] and len(o.sent) == n and J(c.ruling(hx(5))) == {"exists": False})
    module._now = lambda: 1_000_000 + 15 * 60 + 1
    r = J(c.verify(hx(5)))
    check_("after the grace, it is NOT_FILLED", r["verdict"] == "NOT_FILLED" and o.sent[-1][:2] == (key(5), False))

    print("\nwhat the court will not guess")
    tx6 = "66" * 32
    o.add(key(6), tx=bytes.fromhex(tx6))
    web.pages[HORIZON + "/transactions/" + tx6] = (502, b"bad gateway")
    n = len(o.sent)
    r = J(c.verify(hx(6)))
    check_("a network that cannot be read changes nothing", not r["ok"] and "could not be read" in r["error"] and len(o.sent) == n)
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
    t9a, ops9 = stellar_tx(tx9, memo, [pay_op()], created="2026-11-16T09:00:00Z")
    t9b, _ = stellar_tx(tx9, memo, [pay_op()], created="2026-11-16T09:00:05Z")
    web.pages[HORIZON + "/transactions/" + tx9] = [(200, t9a), (200, t9b)]
    web.pages[HORIZON + "/transactions/" + tx9 + "/operations?limit=200"] = (200, ops9)
    n = len(o.sent)
    try:
        c.verify(hx(9))
        failed = False
    except RoundFailed:
        failed = True
    check_("validators that read a different timestamp than the leader do not agree, and nothing is sent", failed and len(o.sent) == n)

    print("\nresending a ruling")
    o.claims[key(4)]["status"] = 1
    r = J(c.resend(hx(4)))
    check_("a recorded ruling that has not landed is sent again, unchanged, with no network read",
           r["ok"] and o.sent[-1][:3] == (key(4), False, 0) and o.sent[-1][3] == hashlib.sha256(("farshore|" + key(4).hex() + "|NOT_FILLED").encode()).digest())
    o.claims[key(4)]["status"] = 3
    check_("a ruling the oracle already holds is not sent again", "already holds" in J(c.resend(hx(4)))["error"])
    check_("a claim the court never ruled on cannot be resent", "has not ruled" in J(c.resend(hx(1)))["error"])

    print("\nreads")
    page = J(c.page(0, 10))
    check_("rulings page newest first", page["total"] == 5 and page["items"][0]["claim"] == hx(8))
    check_("the court publishes which API it reads for each network", J(c.networks())["stellar-testnet"]["api"] == HORIZON and J(c.networks())["base-sepolia"]["asset"] == "ETH")

    passed = sum(1 for _, ok in RESULTS if ok)
    print("\n%d checks, all through the real Farshore court" % len(RESULTS) if passed == len(RESULTS) else "\n%d of %d checks passed" % (passed, len(RESULTS)))
    sys.exit(0 if passed == len(RESULTS) else 1)


if __name__ == "__main__":
    main()
