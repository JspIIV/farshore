# Farshore

**An Open Intents Framework oracle for chains no bridge reaches. Internet Court checks the fill on the far chain's own
public API, and OIF's unmodified settler, at its canonical address, pays the solver.**

Internet Court · GenLayer · Open Intents Framework (LI.FI Intents).

The Open Intents Framework is the intent standard behind LI.FI Intents: a user locks inputs in OIF's input settler and
asks for an output somewhere else; a solver delivers it and is paid when the order's **oracle** proves the fill. OIF's
oracles ride messaging layers (Hyperlane, Wormhole, Polymer) and a Bitcoin light client, so a destination outside them
cannot be proven, and an order for it cannot be made.

Farshore is an oracle for those destinations. It follows the pattern of OIF's own BitcoinOracle, with Internet Court in
the place of a light client: GenLayer validators read the far chain's public API themselves, agree in code whether the
solver paid exactly what the order asked, and the court's own contract writes the OIF attestation. Stellar testnet and
Base Sepolia are wired; another network is a table entry and an adapter.

- **The court (GenLayer Bradbury):** [`0x073885844B08A7F482a37666D145D377bf558d33`](https://scan.bradbury.genlayer.com/address/0x073885844B08A7F482a37666D145D377bf558d33)
- **OIF's InputSettlerEscrow on GenLayer Chain:** `0x1CC9260E285C2C8AC8D2E7102F3978056Ec1d0a8`, the address it has on
  Ethereum, Base, Arbitrum and every chain in OIF's DEPLOYMENTS.md
- **Skill:** [`SKILL.md`](SKILL.md)

```text
user    ──open(order)──▶ OIF InputSettlerEscrow 0x1CC9...d0a8       output: 2.5 XLM to this Stellar account, oracle: Farshore
solver  ──pays on Stellar, memo_hash = memoFor(orderId, solver)
solver  ──claim(orderId, output, txHash)──▶ FarshoreOracle (GenLayer Chain)
anybody ──verify(claimId)──▶ Farshore court (GenLayer): validators read the transaction from Horizon ──▶ FILLED, final
court's Ghost ──settle(claimId, FILLED, Stellar timestamp)──▶ FarshoreOracle: the OIF fill attestation is written
solver  ──finalise(order, [timestamp, solver])──▶ OIF settler pays the inputs           nothing filled: refund after expiry
```

## The parts

| Contract | Where | What |
|---|---|---|
| `InputSettlerEscrow` | GenLayer Chain (4221) | OIF's input settler, **unmodified**: rebuilt from [`oif-contracts`](https://github.com/openintentsframework/oif-contracts) at `b260675` with OIF's exact compiler input and deployed through the same deterministic deployment proxy, so it sits at OIF's canonical address |
| [`FarshoreOracle`](evm/FarshoreOracle.sol) | GenLayer Chain | an OIF input oracle on `BaseInputOracle`: takes solvers' claims, accepts rulings from the court alone, and writes the fill attestation OIF's settler checks |
| [`Farshore`](contracts/farshore.py) | GenLayer Bradbury | the court: reads a claimed payment from the far chain's own API and settles the claim on the oracle itself |
| [`TestUSD`](evm/TestUSD.sol) | GenLayer Chain | the test dollar orders lock as inputs |

Addresses are in [`deployment.json`](deployment.json).

## Why it holds

- **A copied payment pays nobody but its maker.** The memo is `keccak256(orderId, solver)`. Somebody who claims another
  solver's payment is a different solver, so the memo does not match their claim.
- **The solver cannot choose the evidence.** A claim names a transaction hash and nothing else. The court reads the API
  it has for the output's network: Horizon for Stellar testnet, Blockscout for Base Sepolia.
- **Exact or nothing.** FILLED needs exactly the amount, of the native asset, to the recipient, in a successful
  transaction with the memo. One stroop short or over is NOT_FILLED. The check is code, not a model.
- **The far chain's clock.** The attestation carries the transaction's own timestamp, agreed by the round, and OIF's
  settler refuses a fill after the order's deadline.
- **One payment, one fill.** A filled transaction cannot be claimed again for any order.
- **No guesses.** A network that cannot be read changes nothing. A transaction the network does not show changes nothing
  for 15 minutes after the claim, then is NOT_FILLED.
- **Only the court rules, once. No relayer, no owner.** The court calls the oracle itself when its verdict is final.
- **OIF's code, at OIF's address.** The settler on GenLayer Chain is the same init code, through the same proxy and
  salt, as on Ethereum, Base and Base Sepolia, so it lands at the same address.

## Proven on Bradbury

`scripts/prove.mjs`, with every transaction in [`results/proof.json`](results/proof.json). padv opens orders, ppub
solves them, and pchg, a stranger, puts claims to the court.

| Order | Asks for | What happened on the far chain | Court (Bradbury) | On OIF's settler |
|---|---|---|---|---|
| A | 2.5 XLM on Stellar testnet | ppub paid it [`7d224308`](https://stellar.expert/explorer/testnet/tx/7d2243085eed42e8482dfd732fe43cd6e27d4c5f8fd61e6d3650dd28d5332db0). padv, a stranger, claimed it first | [`0x5688530e`](https://scan.bradbury.genlayer.com/tx/0x5688530eced1a360dbdd9261bf92c9caf2ba1599b549e89e3bbe88dcf31bd7f7) **NOT_FILLED**: "the memo binds this payment to another order or another solver" | |
| A | | ppub claimed its own payment | [`0x07c31f9d`](https://scan.bradbury.genlayer.com/tx/0x07c31f9d3fdee94ca8603eff164c2b2faddd7d5ba70af717d65a34f34850850f) **FILLED**: "pays 2.5000000 XLM to GDCVR24S..." | finalised, ppub +25 tUSD [`0x8e757a0d`](https://scan.testnet-chain.genlayer.com/tx/0x8e757a0d741f7f6687da40b2023fda3f953a4c0414a8e3ee5d68bdf86342e3ab) |
| B | 0.0001 ETH on Base Sepolia | ppub paid it [`9613df4e`](https://base-sepolia.blockscout.com/tx/0x9613df4ead896b6f8985e261074b1a979ac159f188baeb5fd2c7fd0691707eba) | [`0xfc7629dd`](https://scan.bradbury.genlayer.com/tx/0xfc7629dd6dcba4c2d5bd1be6fe8839c3f37aa6731ad0aaff4bd0aa4ccd6adf55) **FILLED**: "pays 100000000000000 wei to 0x8051..." | finalised, ppub +10 tUSD [`0x2b585ec8`](https://scan.testnet-chain.genlayer.com/tx/0x2b585ec868b66fd02110c60de0de45bc14f2ddba0ff4539affd2aafe6a8058cc) |
| C | 3 XLM on Stellar testnet | ppub paid 2.9 [`433a9e38`](https://stellar.expert/explorer/testnet/tx/433a9e38d0a68b2bf926d7a5cf79db12f33269c9298e219ca23f6f237e6a589b) | [`0x003b9aca`](https://scan.bradbury.genlayer.com/tx/0x003b9aca237c77d21fb892db168989d4e1afd2692934b309347038ed5857d06d) **NOT_FILLED**: "no operation pays exactly 3.0000000 XLM" | refunded to padv after expiry [`0x08c380b8`](https://scan.testnet-chain.genlayer.com/tx/0x08c380b8308f0280bc28e723a435e0c332d90a6a7e8237ecbd7571e41ce4089c) |
| F | (a claim on A naming a transaction that exists nowhere) | none | first round: no change; after 15 minutes [`0xf0ed4036`](https://scan.bradbury.genlayer.com/tx/0xf0ed4036f5829895dd191194389294af4c652c6616c6520400ed0a261fe49f7d) **NOT_FILLED** | |

Refused, each with `eth_call` from the account that would try it: an output naming another oracle (`not-this-oracle`),
a callback (`no-callback`), a second identical claim (`claimed`), anybody but the court settling (`only-court`), the
court settling twice (`not-claimed`), a filled payment claimed again (`tx-used`), finalising before the fill is proven or
with another timestamp (`NotProven`), anybody but the solver finalising (`NotOrderOwner`), finalising twice
(`InvalidOrderStatus`) and a refund before expiry (`TimestampNotPassed`).

The money, to the unit: padv paid 35 tUSD for the two filled orders and got the unfilled one back, ppub got exactly 35,
and nothing is left in the settler. **All 24 checks pass.**

`node scripts/verify.mjs` checks, with no keys, that the court's deployed source is `contracts/farshore.py` byte for
byte, that every round is final and every claim ended as the proof says, that every payment exists on Stellar testnet
and Base Sepolia through their own APIs, and that each order ended Claimed or Refunded on OIF's settler.

`node scripts/verify_evm.mjs` rebuilds OIF's InputSettlerEscrow from source, shows that its CREATE2 address through the
deterministic deployment proxy with salt 0 is `0x1CC9...d0a8`, and finds that same code at that address on GenLayer
Chain, Ethereum, Base and Base Sepolia (immutables taken from each chain). It compares the oracle and the test dollar
with GenLayer Chain too. All match.

## Tests

`python tests/farshore_rules.py`: 42 checks through the real court against a stub of the runtime, the oracle and the
web: the Stellar account encoding against the Stellar SDK, amounts, exact payments and each way to miss one on both
networks, missing and unreadable transactions, the grace period, validators that read a different time, refusals,
`resend`, and that the court only ever reads its own API for a network.

[`SECURITY.md`](SECURITY.md) maps every trust surface to the line that enforces it and the check that proves it.

## Reproduce

```bash
git clone --recurse-submodules https://github.com/JspIIV/farshore && cd farshore
npm install
python tests/farshore_rules.py
node scripts/verify.mjs && node scripts/verify_evm.mjs
```

## Where it stops, plainly

- Native assets on two testnets. Issued Stellar assets, Solana, TON or Tron are each an API entry and a reader.
- The court sees what the network's public API reports. A deep reorganisation after a ruling is outside what it covers.
- Testnet money only.

## Licence

MIT. `vendor/oif-contracts` stays under its own MIT licence.
