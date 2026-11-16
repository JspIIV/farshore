---
name: farshore-oif-oracle
description: Fill Open Intents Framework orders whose output is on a chain no bridge reaches. Farshore is an OIF input oracle that Internet Court operates: GenLayer validators read the far chain from two independent sources and write the OIF fill attestation themselves, so a solver is paid by OIF's unmodified settler for a payment on Stellar testnet or Base Sepolia. Use when an agent or app opens or fills cross-chain intents and the destination has no messaging layer.
---

# Farshore: an OIF oracle for chains no bridge reaches

The Open Intents Framework (the intent standard behind LI.FI Intents) proves a solver's fill through the oracle the
order names. Its oracles ride messaging layers, so a destination outside them cannot be proven. Farshore is an oracle
for those destinations, and Internet Court is the party that checks them.

```text
user    ──open(order)──▶ OIF InputSettlerEscrow 0x1CC9...d0a8      inputs held; output: pay X of asset to recipient on a far chain
solver  ──pays on the far chain, memo = memoFor(orderId, solver)
solver  ──claim(orderId, output, txHash)──▶ FarshoreOracle
anybody ──verify(claimId)──▶ Farshore court (GenLayer): validators read the transaction from the network's own API
court   ──settle(claimId, FILLED, timestamp)──▶ FarshoreOracle: OIF fill attestation written
solver  ──finalise(order, [timestamp, solver], ...)──▶ OIF settler pays the inputs       (nothing filled: refund after expiry)
```

## Deployed on GenLayer Bradbury (chain 4221)

| Contract | Address | Source |
|---|---|---|
| Farshore court (Intelligent Contract) | `0xe8764C31E6bB401ABda74404c3F466Fd46aAe4AF` | `contracts/farshore.py` |
| FarshoreOracle | `0x005b5950ce7f46b2710c16a7108Ce320c55b370a` | `evm/FarshoreOracle.sol` |
| InputSettlerEscrow | `0x1CC9260E285C2C8AC8D2E7102F3978056Ec1d0a8` | OIF, unmodified, its canonical address on every chain |
| Test USD (inputs; anyone may mint) | `0xd0dD2416Bba75578703532D70674fb6aCAfDEC0e` | `evm/TestUSD.sol` |

RPC for GenLayer writes: `https://rpc-bradbury.genlayer.com` (it also serves GenLayer Chain's EVM methods).

## Networks the court reads

| Output `token` (network, asset) | Recipient | Amount unit | Sources the court reads, both must agree | Memo |
|---|---|---|---|---|
| `stellar-testnet`, `XLM` | raw 32-byte ed25519 key | stroops (1e-7) | Horizon testnet and StellarExpert | `memo_hash` = `memoFor` |
| `base-sepolia`, `ETH` | address, left-padded to 32 bytes | wei | Blockscout v2 and the chain's own RPC | transaction input data = `memoFor` |

`court.policy()` returns the sources, the grace period and how to retry.

The token is two ASCII fields: the network in the high 16 bytes, the asset in the low 16 (`lib/oif.mjs` `tokenTag`).

## Open an order

```js
import { output, order, orderTuple } from './lib/oif.mjs';
const out = output({ oracle: ORACLE, network: 'stellar-testnet', asset: 'XLM', amount: 25_000_000n, recipient: RAW_ED25519_KEY });
const o = order({ user, nonce, inputToken: TUSD, inputAmount, oracle: ORACLE, outputs: [out], fillDeadline, expires });
await tusd.approve(SETTLER, inputAmount);
await settler.open(orderTuple(o));            // orderId = await settler.orderIdentifier(orderTuple(o))
```

## Fill it

1. Pay on the far chain exactly the amount, to the recipient, with `memo = await oracle.memoFor(orderId, yourAddressAs32Bytes)`.
2. `oracle.claim(orderId, outputTuple(out), '0x' + txHash)` from that same address.
3. Ask the court, funding the message that carries the result to the oracle:
   ```js
   const gasLimit = 400000n, maxGasPrice = 1000000000n, budget = gasLimit * maxGasPrice;
   const feeParams = AbiCoder.defaultAbiCoder().encode(['uint256','uint256'], [gasLimit, maxGasPrice]);
   const messageAllocations = [{ messageType: 0, onAcceptance: false, recipient: ORACLE, budget, feeParams }];
   const e = await client.estimateTransactionFees({ totalMessageFees: budget, messageAllocations });
   await client.writeContract({ address: COURT, functionName: 'verify', args: [claimId],
     fees: { distribution: e.distribution, feeValue: e.feeValue, messageAllocations } });
   ```
4. When `oracle.claimFacts(claimId)` shows status 2 (Filled), `settler.finalise(orderTuple(o), [[filledAt, you]], you, '0x')`.

## What the court guarantees

- It reads the network from two sources in its own table; the claim gives a hash and nothing else. Sources that disagree change nothing.
- FILLED only for exactly the amount of the native asset, to the recipient, in a successful transaction carrying the memo
  for this order and this claimer. A payment copied by somebody else is NOT_FILLED for them.
- A transaction the network does not show changes nothing for 15 minutes after the claim, then is NOT_FILLED.
- One far-chain transaction fills one output, once.

## Decision payload

| Field | Where | Meaning |
|---|---|---|
| `verdict` | `court.ruling(claimId).verdict` | `FILLED` or `NOT_FILLED`, agreed by the round |
| `why` | same | what the network showed |
| `evidence` | same | sha256 of network, transaction, verdict, time, memo, recipient, amount; the reason sent to the oracle commits to it |
| `sources` | same | the two sources read |
| `filled_at` | same, and `oracle.claimFacts(claimId)[7]` | the far chain's timestamp, used in `finalise` |
| status | `oracle.claimFacts(claimId)[0]` | 1 Claimed, 2 Filled, 3 NotFilled |

## Limits

- Native assets on Stellar testnet and Base Sepolia. Each new network is a table entry and an adapter in the court.
- Testnet only.
