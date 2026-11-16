# v0.3.0
# { "Depends": "py-genlayer:babwp9t37jw9g4j2bccgn14g6x7x9s1f7dsmh7emvz735pz4zxa0" }
"""Farshore: Internet Court as an Open Intents Framework oracle for outputs on chains no bridge reaches.

An OIF order locks the user's inputs in OIF's input settler on GenLayer Chain and asks for an output on another
network: pay this amount of this asset to this account. A solver pays there, with a memo that binds the payment to the
order and to the solver, and claims the fill on the FarshoreOracle. verify(claim_id), open to anybody, has a GenLayer
round read that transaction from the network's own public API and decide in code:

  FILLED      it pays exactly the amount of the asset to the recipient, with the memo, and succeeded
  NOT_FILLED  it does not, or the network has no such transaction long after the claim

When the verdict is final this contract calls settle() on the oracle itself, through its Ghost on GenLayer Chain. FILLED
writes the OIF attestation with the transaction's own timestamp, and the solver finalises on OIF's settler.

Which API is read is fixed here, per network. The solver names only a transaction hash, so it cannot point the court at
a page of its own. Nothing a transaction says about itself, such as text in a memo, is read as anything but bytes.
"""

import genlayer as gl
import base64
import hashlib
import json
from datetime import datetime, timezone

FILLED = "FILLED"
NOT_FILLED = "NOT_FILLED"
UNREADABLE = "UNREADABLE"
CLAIMED = 1
NOT_FOUND_GRACE = 15 * 60

# network tag -> (asset tag, reader). The tag is the ASCII in the high 16 bytes of the output's token.
HORIZON = {"stellar-testnet": "https://horizon-testnet.stellar.org"}
BLOCKSCOUT = {"base-sepolia": "https://base-sepolia.blockscout.com"}
NATIVE = {"stellar-testnet": "XLM", "base-sepolia": "ETH"}


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


def _iso(ts: str) -> int:
    return int(datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp())


def _get(url: str):
    """(status, json or None). A fetch that fails raises, so the round reports nothing rather than a guess."""
    res = gl.nondet.web.get(url)
    status = int(getattr(res, "status", 0) or 0)
    body = getattr(res, "body", b"") or b""
    try:
        return status, json.loads(_b(body).decode("utf-8"))
    except Exception:
        return status, None


def _unreadable(status: int, doc) -> bool:
    return doc is None or status >= 500 or status == 429


def check_stellar(base: str, tx: str, memo: bytes, recipient: bytes, amount: int) -> dict:
    status, t = _get(base + "/transactions/" + tx)
    if _unreadable(status, t):
        return {"verdict": UNREADABLE}
    if status == 404 or t.get("hash") != tx:
        return {"verdict": "MISSING", "why": "the network has no such transaction"}
    if not t.get("successful", False):
        return {"verdict": NOT_FILLED, "why": "the transaction failed on the network"}
    if t.get("memo_type") != "hash":
        return {"verdict": NOT_FILLED, "why": "the transaction carries no hash memo, so it is not bound to this order and solver"}
    try:
        got = base64.b64decode(str(t.get("memo", "")))
    except Exception:
        got = b""
    if got != memo:
        return {"verdict": NOT_FILLED, "why": "the memo binds this payment to another order or another solver"}
    status, ops = _get(base + "/transactions/" + tx + "/operations?limit=200")
    if _unreadable(status, ops):
        return {"verdict": UNREADABLE}
    want_to, want_amount = stellar_account(recipient), stroops(amount)
    records = ((ops.get("_embedded") or {}).get("records") or []) if isinstance(ops, dict) else []
    for op in records:
        kind = op.get("type")
        if kind == "payment" and op.get("asset_type") == "native" and op.get("to") == want_to and op.get("amount") == want_amount:
            return {"verdict": FILLED, "ts": _iso(t.get("created_at")), "why": "pays " + want_amount + " XLM to " + want_to}
        if kind == "create_account" and op.get("account") == want_to and op.get("starting_balance") == want_amount:
            return {"verdict": FILLED, "ts": _iso(t.get("created_at")), "why": "funds " + want_to + " with " + want_amount + " XLM"}
    return {"verdict": NOT_FILLED, "why": "no operation pays exactly " + want_amount + " XLM to " + want_to}


def check_evm(base: str, tx: str, memo: bytes, recipient: bytes, amount: int) -> dict:
    status, t = _get(base + "/api/v2/transactions/0x" + tx)
    if _unreadable(status, t):
        return {"verdict": UNREADABLE}
    if status == 404 or str(t.get("hash", "")).lower() != "0x" + tx:
        return {"verdict": "MISSING", "why": "the network has no such transaction"}
    if t.get("status") != "ok" or t.get("result") != "success":
        return {"verdict": NOT_FILLED, "why": "the transaction is not a successful one"}
    if str(t.get("raw_input", "")).lower() != "0x" + memo.hex():
        return {"verdict": NOT_FILLED, "why": "the input data binds this payment to another order or another solver"}
    to = str((t.get("to") or {}).get("hash", "")).lower()
    want_to = "0x" + recipient[12:].hex()
    if recipient[:12] != bytes(12) or to != want_to:
        return {"verdict": NOT_FILLED, "why": "it pays " + to + ", not " + want_to}
    if str(t.get("value", "")) != str(amount):
        return {"verdict": NOT_FILLED, "why": "it pays " + str(t.get("value")) + " wei, not " + str(amount)}
    return {"verdict": FILLED, "ts": _iso(t.get("timestamp")), "why": "pays " + str(amount) + " wei to " + want_to}


def check(network: str, tx: str, memo: bytes, recipient: bytes, amount: int) -> dict:
    try:
        if network in HORIZON:
            return check_stellar(HORIZON[network], tx, memo, recipient, amount)
        if network in BLOCKSCOUT:
            return check_evm(BLOCKSCOUT[network], tx, memo, recipient, amount)
    except Exception:
        return {"verdict": UNREADABLE}
    return {"verdict": NOT_FILLED, "why": "the court reads no network called " + repr(network)}


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
        """Check a claimed fill on its own network and settle it on the oracle. Open to anybody."""
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

        if network in NATIVE and asset != NATIVE[network]:
            got = {"verdict": NOT_FILLED, "why": "the court checks only " + NATIVE[network] + " on " + network}
        else:
            def lead() -> dict:
                return check(network, tx, memo, recipient, amount)

            def agree(leaders_res) -> bool:
                if not isinstance(leaders_res, gl.vm.Return) or not isinstance(leaders_res.calldata, dict):
                    return False
                mine, theirs = check(network, tx, memo, recipient, amount), leaders_res.calldata
                return mine.get("verdict") == theirs.get("verdict") and mine.get("ts", 0) == theirs.get("ts", 0)

            got = gl.vm.run_nondet(lead, agree)
        verdict = str(got.get("verdict", "")) if isinstance(got, dict) else ""
        if verdict == "MISSING":
            if _now() - claimed_at < NOT_FOUND_GRACE:
                return json.dumps({"ok": False, "error": "the network does not show this transaction yet; ask again later"})
            verdict = NOT_FILLED
        if verdict not in (FILLED, NOT_FILLED):
            return json.dumps({"ok": False, "error": "the network could not be read; nothing changed"})

        filled_at = int(got.get("ts", 0)) if verdict == FILLED else 0
        why = str(got.get("why", ""))[:300]
        reason = hashlib.sha256(("farshore|" + h + "|" + verdict).encode("utf-8")).digest()
        record = {"claim": "0x" + h, "verdict": verdict, "why": why, "network": network, "asset": asset,
                  "tx": tx, "order": "0x" + order_id.hex(), "solver": "0x" + solver[12:].hex(), "amount": str(amount),
                  "recipient": "0x" + recipient.hex(), "filled_at": filled_at, "ruled_at": _now(),
                  "by": gl.message.sender_address.as_hex.lower(), "reason": reason.hex()}
        self.ids.append(h)
        self.rulings[h] = json.dumps(record)
        oracle.emit().settle(key, verdict == FILLED, filled_at, reason)
        return json.dumps({"ok": True, "verdict": verdict, "filled_at": filled_at})

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
    def networks(self) -> str:
        return json.dumps({n: {"asset": NATIVE[n], "api": (HORIZON.get(n) or BLOCKSCOUT.get(n))} for n in NATIVE})

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
