# Farshore

**An Open Intents Framework oracle for chains no bridge reaches. Internet Court checks the fill on the far chain's own
public API, and OIF's unmodified settler, at its canonical address, pays the solver.**

Internet Court · GenLayer · Open Intents Framework (LI.FI Intents).

The Open Intents Framework is the intent standard behind LI.FI Intents: a user locks inputs in OIF's input settler and
asks for an output somewhere else; a solver delivers it and is paid when the order's **oracle** proves the fill. OIF's
oracles ride messaging layers (Hyperlane, Wormhole, Polymer) and a Bitcoin light client, so a destination outside them
cannot be proven, and an order for it cannot be made.

Farshore is an oracle for those destinations. It follows the pattern of OIF's own BitcoinOracle, with Internet Court in
the place of a light client: GenLayer validators read the far chain's record from two independent sources themselves,
agree in code whether the solver paid exactly what the order asked, and the court's own contract writes the OIF attestation. Stellar testnet and
Base Sepolia are wired; another network is a table entry and an adapter.

- **The court (GenLayer Bradbury):** [`0xe8764C31E6bB401ABda74404c3F466Fd46aAe4AF`](https://scan.bradbury.genlayer.com/address/0xe8764C31E6bB401ABda74404c3F466Fd46aAe4AF)
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
| [`Farshore`](contracts/farshore.py) | GenLayer Bradbury | the court: reads a claimed payment from two independent sources for the far chain and settles the claim on the oracle itself |
| [`TestUSD`](evm/TestUSD.sol) | GenLayer Chain | the test dollar orders lock as inputs |

Addresses are in [`deployment.json`](deployment.json).

## Why it holds

- **A copied payment pays nobody but its maker.** The memo is `keccak256(orderId, solver)`. Somebody who claims another
  solver's payment is a different solver, so the memo does not match their claim.
- **The solver cannot choose the evidence.** A claim names a transaction hash and nothing else. The court reads the
  sources it has for the output's network.
- **Two independent sources must agree.** Stellar testnet is read from SDF's Horizon and from StellarExpert's API (the
  raw transaction envelope, decoded in the contract); Base Sepolia from Blockscout and from the chain's own RPC. Each is
  reduced to the same facts and judged by the same rule. If they differ on the verdict or the time, nothing changes.
- **An auditable ruling.** Each ruling records an evidence digest, sha256 of network, transaction, verdict, time, memo,
  recipient and amount, and the reason sent to the oracle commits to it. Anyone can recompute it.
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

| Order | Asks for | What happened on the far chain | Court (Bradbury), read from two sources | On OIF's settler |
|---|---|---|---|---|
| A | 2.5 XLM on Stellar testnet | ppub paid it [`850b35c4`](https://stellar.expert/explorer/testnet/tx/850b35c4963748ce537d1548b1a474064a52be962c0add6825adc72c4b6f1db6). padv, a stranger, claimed it first | [`0xd5a1452e`](https://scan.bradbury.genlayer.com/tx/0xd5a1452e90ff63346401d5df5a431ef6e2e3b0020aaae31429c3b0bd9f1c9cdf) **NOT_FILLED**: "the memo binds this payment to another order or another solver" | |
| A | | ppub claimed its own payment | [`0x8ab32041`](https://scan.bradbury.genlayer.com/tx/0x8ab320411080e1876be9aca48316bba065a1bc714434c04050726896a4018d15) **FILLED**: "pays 2.5000000 XLM to GDCVR24S...", from Horizon and StellarExpert | finalised, ppub +25 tUSD [`0xfe674bc0`](https://scan.testnet-chain.genlayer.com/tx/0xfe674bc0653a879a7a9630e79d1dc815e46cc4e8c1ccf3eaf678f5878f1b1e61) |
| B | 0.0001 ETH on Base Sepolia | ppub paid it [`41e55574`](https://base-sepolia.blockscout.com/tx/0x41e5557448b79c954121c5ced31c8ae37c7fe4455b19505a016addf128fc2443) | [`0x7a8557f0`](https://scan.bradbury.genlayer.com/tx/0x7a8557f0904a0ca4c3f6db568e4d1aeb0c0e200864b1807e814289938995fda9) **FILLED**: "pays 100000000000000 wei to 0x8051...", from Blockscout and the chain RPC | finalised, ppub +10 tUSD [`0xc26f962e`](https://scan.testnet-chain.genlayer.com/tx/0xc26f962eba49b206226249a6a2a2b08f23ec5e3d2f90048c4ba197713199e294) |
| C | 3 XLM on Stellar testnet | ppub paid 2.9 [`926f9cd0`](https://stellar.expert/explorer/testnet/tx/926f9cd0cf526bb30453dd518e453e06a805f45f5a8da3e143ce245fae5b5124) | [`0xc73c3b02`](https://scan.bradbury.genlayer.com/tx/0xc73c3b0218a91bccb19c487f7bfb4f5eb4b2ad3eec2dd3f2c19d2548d8859a21) **NOT_FILLED**: "no operation pays exactly 3.0000000 XLM" | refunded to padv after expiry [`0xba2d97eb`](https://scan.testnet-chain.genlayer.com/tx/0xba2d97eb71a4f9e9a0a502e3713ee8a92b2bd756792c08d33cc846ebb267bf10) |
| F | (a claim on A naming a transaction that exists nowhere) | none | first round: no change; after 15 minutes [`0x33e91233`](https://scan.bradbury.genlayer.com/tx/0x33e912339f8b5be3fbf69b6659982c9119ecbb807d2517d5dd3a9813fc0535fd) **NOT_FILLED** | |

Refused, each with `eth_call` from the account that would try it: an output naming another oracle (`not-this-oracle`),
a callback (`no-callback`), a second identical claim (`claimed`), anybody but the court settling (`only-court`), the
court settling twice (`not-claimed`), a filled payment claimed again (`tx-used`), finalising before the fill is proven or
with another timestamp (`NotProven`), anybody but the solver finalising (`NotOrderOwner`), finalising twice
(`InvalidOrderStatus`) and a refund before expiry (`TimestampNotPassed`).

The money, to the unit: padv paid 35 tUSD for the two filled orders and got the unfilled one back, ppub got exactly 35,
and nothing is left in the settler. Each ruling names its two sources and carries an evidence digest, which the proof
recomputes for A from the claim and the payment. **All 26 checks pass.**

`node scripts/verify.mjs` checks, with no keys, that the court's deployed source is `contracts/farshore.py` byte for
byte, that every round is final and every claim ended as the proof says, that every payment exists on Stellar testnet
and Base Sepolia through their own APIs, and that each order ended Claimed or Refunded on OIF's settler.

`node scripts/verify_evm.mjs` rebuilds OIF's InputSettlerEscrow from source, shows that its CREATE2 address through the
deterministic deployment proxy with salt 0 is `0x1CC9...d0a8`, and finds that same code at that address on GenLayer
Chain, Ethereum, Base and Base Sepolia (immutables taken from each chain). It compares the oracle and the test dollar
with GenLayer Chain too. All match.

## Tests

`python tests/farshore_rules.py`: 48 checks through the real court against a stub of the runtime, the oracle and the
web: the Stellar account encoding against the Stellar SDK, a real Stellar envelope decoded in the contract, amounts,
exact payments and each way to miss one on both networks, every way the two sources can disagree, missing and unreadable transactions, the grace period, validators that read a different time, refusals,
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
- The court trusts that two independent public sources for a network do not report the same false transaction. If both
  did, the ruling would follow them. A deep reorganisation after a ruling is outside what it covers.
- Testnet money only.

## Licence

MIT. `vendor/oif-contracts` stays under its own MIT licence.
