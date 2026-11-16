# v0.3.0
# { "Depends": "py-genlayer:babwp9t37jw9g4j2bccgn14g6x7x9s1f7dsmh7emvz735pz4zxa0" }
"""Farshore: Internet Court as an Open Intents Framework oracle for outputs on chains no bridge reaches.

An OIF order locks the user's inputs in OIF's input settler on GenLayer Chain and asks for an output on another
network: pay this amount of this asset to this account. A solver pays there, with a memo that binds the payment to the
order and to the solver, and claims the fill on the FarshoreOracle. verify(claim_id), open to anybody, has a GenLayer
round read that transaction from TWO independent sources for the network and decide in code:

  FILLED      both sources show exactly the amount of the asset paid to the recipient, with the memo, succeeded
  NOT_FILLED  both sources show it does not, or neither shows the transaction long after the claim

Each source is reduced to the same facts (found, succeeded, memo, payments, time) and judged by the same rule. If the two
sources do not agree on the verdict, the time and the evidence, nothing changes and anybody may ask again. When the
verdict is final this contract calls settle() on the oracle itself, through its Ghost on GenLayer Chain, with a reason
that commits to the evidence. FILLED writes the OIF attestation with the transaction's own timestamp.

The sources are fixed here, per network. The solver names only a transaction hash, so it cannot point the court at a
page of its own. Nothing a transaction says about itself, such as text in a memo, is read as anything but bytes.
"""

import genlayer as gl
import base64
import hashlib
import json
from datetime import datetime, timezone

FILLED = "FILLED"
NOT_FILLED = "NOT_FILLED"
UNREADABLE = "UNREADABLE"
MISSING = "MISSING"
SPLIT = "SPLIT"
CLAIMED = 1
GRACE_SECONDS = 15 * 60

# Every network the court reads: its native asset, how its records are read, and two independent sources.
NETWORKS = {
    "stellar-testnet": {"asset": "XLM", "kind": "stellar",
                        "primary": "https://horizon-testnet.stellar.org",
                        "second": "https://api.stellar.expert/explorer/testnet"},
    "base-sepolia": {"asset": "ETH", "kind": "evm",
                     "primary": "https://base-sepolia.blockscout.com",
                     "second": "https://sepolia.base.org"},
}


def _now() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _hex32(value: str) -> str:
    text = str(value).strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    return text if len(text) == 64 and all(ch in "0123456789abcdef" for ch in text) else ""


def _b(value) -> bytes:
    return bytes(value) if not isinstance(value, bytes) else value


def _tag(raw: bytes) -> str:
    try:
        return raw.rstrip(b"\x00").decode("ascii")
    except Exception:
        return ""


def _crc16_xmodem(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


def stellar_account(pubkey: bytes) -> str:
    """The G... strkey of a raw ed25519 public key (version byte 6 << 3, CRC16-XModem little-endian, base32)."""
    data = bytes([6 << 3]) + pubkey
    crc = _crc16_xmodem(data)
    return base64.b32encode(data + bytes([crc & 0xFF, crc >> 8])).decode("ascii").rstrip("=")


def stroops(amount: int) -> str:
    return str(amount // 10_000_000) + "." + str(amount % 10_000_000).rjust(7, "0")


def _stroops_of(text) -> int:
    whole, _, frac = str(text).partition(".")
    return int(whole) * 10_000_000 + int((frac + "0000000")[:7])


def _iso(ts: str) -> int:
    return int(datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp())


def _json(res):
    """(status, json or None)."""
    status = int(getattr(res, "status", 0) or 0)
    body = getattr(res, "body", b"") or b""
    try:
        return status, json.loads(_b(body).decode("utf-8"))
    except Exception:
        return status, None


def _get(url: str):
    return _json(gl.nondet.web.get(url))


def _rpc(url: str, calls: list):
    payload = [{"jsonrpc": "2.0", "id": i, "method": m, "params": p} for i, (m, p) in enumerate(calls)]
    status, doc = _json(gl.nondet.web.post(url, body=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}))
    if status >= 400 or not isinstance(doc, list):
        return None
    by_id = {d.get("id"): d for d in doc if isinstance(d, dict)}
    out = []
    for i in range(len(calls)):
        d = by_id.get(i)
        if d is None or "error" in d:
            return None
        out.append(d.get("result"))
    return out


def _unreadable(status: int, doc) -> bool:
    return doc is None or status >= 500 or status == 429


# ------------------------------------------------------------------ Stellar XDR, as much as a payment needs

class _Xdr:
    def __init__(self, data: bytes):
        self.d, self.i = data, 0

    def take(self, n: int) -> bytes:
        if self.i + n > len(self.d):
            raise ValueError("short xdr")
        out = self.d[self.i:self.i + n]
        self.i += n
        return out

    def u32(self) -> int:
        return int.from_bytes(self.take(4), "big")

    def i64(self) -> int:
        return int.from_bytes(self.take(8), "big", signed=True)

    def fixed(self, n: int) -> bytes:
        out = self.take(n)
        self.take((4 - n % 4) % 4)
        return out

    def var(self) -> bytes:
        return self.fixed(self.u32())

    def muxed(self) -> bytes:
        kind = self.u32()
        if kind == 0x100:
            self.take(8)
        elif kind != 0:
            raise ValueError("muxed account type")
        return self.take(32)

    def account(self) -> bytes:
        if self.u32() != 0:
            raise ValueError("public key type")
        return self.take(32)

    def preconditions(self) -> None:
        kind = self.u32()
        if kind == 1:
            self.take(16)
        elif kind == 2:
            if self.u32():
                self.take(16)
            if self.u32():
                self.take(8)
            if self.u32():
                self.take(8)
            self.take(12)
            for _ in range(self.u32()):
                signer = self.u32()
                self.take(32)
                if signer == 3:
                    self.var()

    def memo(self):
        kind = self.u32()
        if kind == 0:
            return None
        if kind == 1:
            self.var()
            return b"text"
        if kind == 2:
            self.take(8)
            return b"id"
        if kind in (3, 4):
            got = self.take(32)
            return got if kind == 3 else b"return"
        raise ValueError("memo type")

    def operations(self) -> list:
        pays = []
        for _ in range(self.u32()):
            if self.u32():
                self.muxed()
            kind = self.u32()
            if kind == 0:                                   # CREATE_ACCOUNT
                to = self.account()
                pays.append((stellar_account(to), "XLM", self.i64()))
            elif kind == 1:                                 # PAYMENT
                to = self.muxed()
                asset = self.u32()
                if asset == 1:
                    self.take(4)
                    self.account()
                elif asset == 2:
                    self.take(12)
                    self.account()
                pays.append((stellar_account(to), "XLM" if asset == 0 else "other", self.i64()))
            else:
                break                                       # an operation this reader does not size: stop here
        return pays


def stellar_envelope(body_b64: str) -> dict:
    """(memo, payments) from a base64 TransactionEnvelope."""
    x = _Xdr(base64.b64decode(body_b64))
    kind = x.u32()
    if kind == 5:                                           # fee bump: skip to the inner transaction
        x.muxed()
        x.i64()
        if x.u32() != 2:
            raise ValueError("inner envelope type")
        kind = 2
    if kind == 2:
        x.muxed()
        x.take(4 + 8)
        x.preconditions()
    elif kind == 0:
        x.take(32 + 4 + 8)
        if x.u32():
            x.take(16)
    else:
        raise ValueError("envelope type")
    memo = x.memo()
    return {"memo": memo, "pays": x.operations()}


def stellar_result_ok(result_b64: str) -> bool:
    x = _Xdr(base64.b64decode(result_b64))
    x.i64()
    code = int.from_bytes(x.take(4), "big", signed=True)
    if code == 1:                                           # fee bump whose inner transaction ran
        x.take(32)
        x.i64()
        code = int.from_bytes(x.take(4), "big", signed=True)
    return code == 0


# ------------------------------------------------------------------ the four readers, reduced to the same facts
# facts: None (could not read) | {"found": False} | {"found": True, "ok": bool, "memo": bytes|None, "pays": [(to, asset, amount)], "ts": int}

def read_horizon(base: str, tx: str):
    status, t = _get(base + "/transactions/" + tx)
    if _unreadable(status, t):
        return None
    if status == 404 or t.get("hash") != tx:
        return {"found": False}
    memo = None
    if t.get("memo_type") == "hash":
        try:
            memo = base64.b64decode(str(t.get("memo", "")))
        except Exception:
            memo = b"bad"
    elif t.get("memo_type") not in (None, "none"):
        memo = b"other"
    status, ops = _get(base + "/transactions/" + tx + "/operations?limit=200")
    if _unreadable(status, ops):
        return None
    pays = []
    for op in ((ops.get("_embedded") or {}).get("records") or []) if isinstance(ops, dict) else []:
        if op.get("type") == "payment":
            pays.append((op.get("to"), "XLM" if op.get("asset_type") == "native" else "other", _stroops_of(op.get("amount", "0"))))
        elif op.get("type") == "create_account":
            pays.append((op.get("account"), "XLM", _stroops_of(op.get("starting_balance", "0"))))
    return {"found": True, "ok": bool(t.get("successful", False)), "memo": memo, "pays": pays, "ts": _iso(t.get("created_at"))}


def read_stellar_expert(base: str, tx: str):
    status, t = _get(base + "/tx/" + tx)
    if status == 404 and isinstance(t, dict):
        return {"found": False}
    if _unreadable(status, t):
        return None
    if t.get("hash") != tx:
        return {"found": False}
    env = stellar_envelope(str(t.get("body", "")))
    return {"found": True, "ok": stellar_result_ok(str(t.get("result", ""))), "memo": env["memo"], "pays": env["pays"], "ts": int(t.get("ts", 0))}


def read_blockscout(base: str, tx: str):
    status, t = _get(base + "/api/v2/transactions/0x" + tx)
    if _unreadable(status, t):
        return None
    if status == 404 or str(t.get("hash", "")).lower() != "0x" + tx:
        return {"found": False}
    raw = str(t.get("raw_input", "")).lower()
    to = str((t.get("to") or {}).get("hash", "")).lower()
    return {"found": True, "ok": t.get("status") == "ok" and t.get("result") == "success",
            "memo": bytes.fromhex(raw[2:]) if raw.startswith("0x") and len(raw) == 66 else b"other",
            "pays": [(to, "ETH", int(str(t.get("value", "0"))))], "ts": _iso(t.get("timestamp"))}


def read_evm_rpc(url: str, tx: str):
    got = _rpc(url, [("eth_getTransactionByHash", ["0x" + tx]), ("eth_getTransactionReceipt", ["0x" + tx])])
    if got is None:
        return None
    t, r = got
    if t is None:
        return {"found": False}
    if r is None:
        return {"found": False}
    block = _rpc(url, [("eth_getBlockByNumber", [r.get("blockNumber"), False])])
    if block is None or block[0] is None:
        return None
    raw = str(t.get("input", "")).lower()
    return {"found": True, "ok": str(r.get("status")) == "0x1",
            "memo": bytes.fromhex(raw[2:]) if raw.startswith("0x") and len(raw) == 66 else b"other",
            "pays": [(str(t.get("to") or "").lower(), "ETH", int(str(t.get("value", "0x0")), 16))], "ts": int(str(block[0].get("timestamp", "0x0")), 16)}


READERS = {"stellar": (read_horizon, read_stellar_expert), "evm": (read_blockscout, read_evm_rpc)}


def recipient_of(kind: str, recipient: bytes) -> str:
    if kind == "stellar":
        return stellar_account(recipient)
    return "0x" + recipient[12:].hex() if recipient[:12] == bytes(12) else ""


def judge(facts, kind: str, memo: bytes, recipient: bytes, amount: int) -> dict:
    """One source's facts against the order. The same rule for every source."""
    if facts is None:
        return {"verdict": UNREADABLE}
    if not facts.get("found"):
        return {"verdict": MISSING, "why": "the network has no such transaction"}
    want_to = recipient_of(kind, recipient)
    asset = "XLM" if kind == "stellar" else "ETH"
    shown = stroops(amount) + " XLM" if kind == "stellar" else str(amount) + " wei"
    if not facts.get("ok"):
        return {"verdict": NOT_FILLED, "why": "the transaction failed on the network"}
    if facts.get("memo") != memo:
        return {"verdict": NOT_FILLED, "why": "the memo binds this payment to another order or another solver"}
    if not want_to:
        return {"verdict": NOT_FILLED, "why": "the recipient is not an account on this network"}
    for to, a, n in facts.get("pays") or []:
        if to == want_to and a == asset and n == amount:
            return {"verdict": FILLED, "ts": int(facts.get("ts", 0)), "why": "pays " + shown + " to " + want_to}
    return {"verdict": NOT_FILLED, "why": "no operation pays exactly " + shown + " to " + want_to}


def evidence(network: str, tx: str, verdict: str, ts: int, memo: bytes, recipient: bytes, amount: int) -> str:
    """A digest of what the round agreed: anyone can recompute it from the network and the claim."""
    line = "|".join([network, tx, verdict, str(ts), memo.hex(), recipient.hex(), str(amount)])
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def check(network: str, tx: str, memo: bytes, recipient: bytes, amount: int) -> dict:
    """Both sources, judged alike. They must agree on the verdict and the time, or nothing is decided."""
    spec = NETWORKS.get(network)
    if spec is None:
        return {"verdict": NOT_FILLED, "why": "the court reads no network called " + repr(network)}
    first, second = READERS[spec["kind"]]
    try:
        a = judge(first(spec["primary"], tx), spec["kind"], memo, recipient, amount)
        b = judge(second(spec["second"], tx), spec["kind"], memo, recipient, amount)
    except Exception:
        return {"verdict": UNREADABLE}
    if UNREADABLE in (a["verdict"], b["verdict"]):
        return {"verdict": UNREADABLE}
    if a["verdict"] != b["verdict"] or a.get("ts", 0) != b.get("ts", 0):
        return {"verdict": SPLIT, "why": "the two sources do not agree yet: " + a["verdict"] + " and " + b["verdict"]}
    out = dict(a)
    out["evidence"] = evidence(network, tx, a["verdict"], a.get("ts", 0), memo, recipient, amount)
    return out


@gl.evm.contract_interface
class Oracle:
    class View:
        def claimFacts(self, claim_id: gl.evm.bytes32, /) -> tuple[gl.u8, gl.evm.bytes32, gl.evm.bytes32, gl.evm.bytes32, gl.evm.bytes32, gl.u256, gl.evm.bytes32, gl.u256]: ...
        def claimTimes(self, claim_id: gl.evm.bytes32, /) -> tuple[gl.u256, gl.evm.bytes32]: ...
        def memoFor(self, order_id: gl.evm.bytes32, solver: gl.evm.bytes32, /) -> gl.evm.bytes32: ...

    class Write:
        def settle(self, claim_id: gl.evm.bytes32, filled: bool, filled_at: gl.u32, reason: gl.evm.bytes32, /) -> None: ...


class Farshore(gl.contract.Contract):
    oracle: str
    # claim id hex -> ruling JSON
    rulings: gl.storage.TreeMap[str, str]
    ids: gl.storage.DynArray[str]

    def __init__(self, oracle: str) -> None:
        self.oracle = gl.Address(oracle).as_hex.lower()

    @gl.public.write
    def verify(self, claim_id: str) -> str:
        """Check a claimed fill on its own network, from two sources, and settle it on the oracle. Open to anybody."""
        h = _hex32(claim_id)
        if not h:
            return json.dumps({"ok": False, "error": "give the claim id as 32 bytes of hex"})
        if self.rulings.get(h, None) is not None:
            return json.dumps({"ok": False, "error": "the court has already ruled on this claim"})
        key = bytes.fromhex(h)
        oracle = Oracle(gl.Address(self.oracle))
        f = oracle.view().claimFacts(key)
        if int(f[0]) != CLAIMED:
            return json.dumps({"ok": False, "error": "there is no open claim with this id"})
        order_id, solver, tx_hash, token = _b(f[1]), _b(f[2]), _b(f[3]), _b(f[4])
        amount, recipient = int(f[5]), _b(f[6])
        network, asset = _tag(token[:16]), _tag(token[16:])
        claimed_at = int(oracle.view().claimTimes(key)[0])
        memo = _b(oracle.view().memoFor(order_id, solver))
        tx = tx_hash.hex()

        spec = NETWORKS.get(network)
        if spec is not None and asset != spec["asset"]:
            got = {"verdict": NOT_FILLED, "why": "the court checks only " + spec["asset"] + " on " + network}
        else:
            def lead() -> dict:
                return check(network, tx, memo, recipient, amount)

            def agree(leaders_res) -> bool:
                if not isinstance(leaders_res, gl.vm.Return) or not isinstance(leaders_res.calldata, dict):
                    return False
                mine, theirs = check(network, tx, memo, recipient, amount), leaders_res.calldata
                return all(mine.get(k) == theirs.get(k) for k in ("verdict", "ts", "evidence"))

            got = gl.vm.run_nondet(lead, agree)
        verdict = str(got.get("verdict", "")) if isinstance(got, dict) else ""
        if verdict == SPLIT:
            return json.dumps({"ok": False, "error": got.get("why", "the sources do not agree") + "; nothing changed, ask again later"})
        if verdict == MISSING:
            if _now() - claimed_at < GRACE_SECONDS:
                return json.dumps({"ok": False, "error": "the network does not show this transaction yet; ask again after " + str(claimed_at + GRACE_SECONDS)})
            verdict = NOT_FILLED
            got = dict(got)
            got["evidence"] = evidence(network, tx, MISSING, 0, memo, recipient, amount)
        if verdict not in (FILLED, NOT_FILLED):
            return json.dumps({"ok": False, "error": "the network could not be read; nothing changed"})

        filled_at = int(got.get("ts", 0)) if verdict == FILLED else 0
        ev = str(got.get("evidence", "") or evidence(network, tx, verdict, filled_at, memo, recipient, amount))
        reason = hashlib.sha256(("farshore|" + h + "|" + verdict + "|" + ev).encode("utf-8")).digest()
        record = {"claim": "0x" + h, "verdict": verdict, "why": str(got.get("why", ""))[:300], "network": network, "asset": asset,
                  "tx": tx, "order": "0x" + order_id.hex(), "solver": "0x" + solver[12:].hex(), "amount": str(amount),
                  "recipient": "0x" + recipient.hex(), "filled_at": filled_at, "evidence": ev,
                  "sources": [spec["primary"], spec["second"]] if spec else [], "ruled_at": _now(),
                  "by": gl.message.sender_address.as_hex.lower(), "reason": reason.hex()}
        self.ids.append(h)
        self.rulings[h] = json.dumps(record)
        oracle.emit().settle(key, verdict == FILLED, filled_at, reason)
        return json.dumps({"ok": True, "verdict": verdict, "filled_at": filled_at, "evidence": ev})

    @gl.public.write
    def resend(self, claim_id: str) -> str:
        """Repeat a recorded ruling, with no network read, if it has not reached the oracle."""
        h = _hex32(claim_id)
        stored = self.rulings.get(h, None) if h else None
        if stored is None:
            return json.dumps({"ok": False, "error": "the court has not ruled on this claim"})
        key = bytes.fromhex(h)
        if int(Oracle(gl.Address(self.oracle)).view().claimFacts(key)[0]) != CLAIMED:
            return json.dumps({"ok": False, "error": "the oracle already holds the ruling"})
        r = json.loads(stored)
        Oracle(gl.Address(self.oracle)).emit().settle(key, r["verdict"] == FILLED, int(r["filled_at"]), bytes.fromhex(r["reason"]))
        return json.dumps({"ok": True})

    # ------------------------------------------------------------------ reads

    @gl.public.view
    def ruling(self, claim_id: str) -> str:
        stored = self.rulings.get(_hex32(claim_id), None)
        return stored if stored is not None else json.dumps({"exists": False})

    @gl.public.view
    def oracle_address(self) -> str:
        return self.oracle

    @gl.public.view
    def policy(self) -> str:
        """What the court reads and when it decides: for integrators that retry."""
        return json.dumps({"grace_seconds": GRACE_SECONDS, "networks": NETWORKS,
                           "retry": "UNREADABLE, SPLIT and MISSING inside the grace change nothing; anybody may call verify again",
                           "evidence": "sha256(network|tx|verdict|timestamp|memo hex|recipient hex|amount)"})

    @gl.public.view
    def page(self, start: int, count: int) -> str:
        total = len(self.ids)
        begin = max(0, int(start))
        want = min(50, max(1, int(count)))
        out = []
        pos = total - 1 - begin
        while pos >= 0 and len(out) < want:
            out.append(json.loads(self.rulings[self.ids[pos]]))
            pos -= 1
        return json.dumps({"total": total, "items": out})
