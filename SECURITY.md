# Security notes

What Farshore trusts, what it does not, and where each guarantee is enforced. Line numbers refer to
[`evm/FarshoreOracle.sol`](evm/FarshoreOracle.sol) and [`contracts/farshore.py`](contracts/farshore.py) in this commit.

Every point below is checked:
- on chain, in the proof (`scripts/prove.mjs`, `results/proof.json`), which runs every refusal with `eth_call` from the
  account that would try it, and real orders through every way out;
- in `python tests/farshore_rules.py`, through the real court against a stub of the runtime, the oracle and the web (42 checks);
- by `node scripts/verify_evm.mjs`, which rebuilds OIF's input settler from source and finds it at its canonical address
  on GenLayer Chain, Ethereum, Base and Base Sepolia, and compares the oracle and the test dollar with GenLayer Chain.

## Where the money is

In the Open Intents Framework's own `InputSettlerEscrow`, unmodified, at the address it has on every chain OIF lists
(`0x1CC9260E285C2C8AC8D2E7102F3978056Ec1d0a8`). The settler pays an order's inputs to its solver only when the order's
oracle proves every output, and refunds the user after the order expires. Farshore's oracle never holds money. The court
can only make the oracle say "this output was filled at this time" or refuse to; it cannot pay anybody.

## Trust surfaces

| Surface | Who can act | Enforced at | Checked by |
|---|---|---|---|
| What the user asked for | the user, in the order it signs or opens: network, asset, exact amount, recipient, deadline, expiry | OIF's settler hashes the order into its id; the fill description repeats every output field | proof: three orders opened on the canonical settler |
| Which outputs this oracle will speak for | outputs that name it as oracle and settler, on this chain, with no callback | `FarshoreOracle.sol:86-88` | proof: `not-this-oracle`, `no-callback` |
| Who is paid for a fill | the solver whose address is bound into the payment's memo: `memoFor(order, solver)`, a hash of both | `FarshoreOracle.sol:75`, the claimer is `msg.sender` at `:91`; the court compares the memo at `farshore.py:110-117`, `:139` | proof: a stranger claiming the solver's real payment got NOT_FILLED, then the solver's own claim on it was FILLED |
| Which page the court reads | the court, from a fixed table per network: Horizon testnet for Stellar, Blockscout for Base Sepolia. The claim supplies only a 32-byte hash | `farshore.py:33-35`, `:149-157` | tests: an unknown network or asset is refused without reading anything |
| What counts as a fill | exactly the amount, of the native asset, to the recipient, in a successful transaction with the memo | `farshore.py:100-146`, in code, with no model | tests: one stroop short or over, another asset, another recipient, a failed or reverted transaction, a text memo, all NOT_FILLED |
| When it was filled | the far chain's own timestamp for the transaction, agreed by the round; the settler then refuses a fill after the order's fill deadline | `farshore.py:206-212`; OIF's `_validateFills` | tests: validators that read a different time do not agree; proof: finalise with another timestamp reverts `NotProven` |
| A payment used twice | never: a filled transaction is recorded and cannot be claimed again, for any order | `FarshoreOracle.sol:90`, `:110-112` | proof: `tx-used` |
| A transaction that does not exist | no change until 15 minutes after the claim, then NOT_FILLED | `farshore.py:214-217` | proof: F, both stages |
| A network that cannot be read | no change, no guess; anybody may ask again | `farshore.py:96-98`, `:218-219` | tests |
| Ruling | the court only, named once by the deployer; once per claim | `FarshoreOracle.sol:67-72`, `:104-106` | proof: `only-court`, `not-claimed` on a second settlement |
| Getting paid | the solver alone finalises on OIF's settler, with the proven timestamp | OIF's `finalise` | proof: a stranger reverts `NotOrderOwner`; a second finalise reverts `InvalidOrderStatus` |
| Nothing filled | the settler's own `refund` returns the inputs to the user after expiry, whatever the court does | OIF's `refund` | proof: C refunded by a stranger after expiry |

## Questions a reviewer asks

**Can somebody who watches the far chain steal a solver's fill?**
No. The payment's memo is `keccak256(orderId, solver)`. A stranger who claims it is a different solver, so the memo does
not match its claim and the court rules NOT_FILLED. On chain, padv's claim on ppub's Stellar payment was NOT_FILLED and
ppub's own claim on the same payment was then FILLED.

**Can the solver point the court at a page it controls?**
No. The claim carries a transaction hash and nothing else. The court builds the URL from its own table for the output's
network; a network outside the table is refused before any read.

**Is there a model in the loop?**
No. Every check is code over the network's JSON. GenLayer's part is that every validator fetches the network itself and
the round agrees on the verdict and the timestamp, so no single node or relayer says what happened on the far chain.

**What if the far chain reorganises?**
Stellar transactions are final once included. Base Sepolia's explorer reports confirmed transactions; a deeper
reorganisation after a ruling is outside what this oracle covers, as with any oracle that attests a block it has seen.

**Who can change the rules?**
Nobody. The oracle has no owner, no pause and no upgrade; the deployer named the court once. OIF's settler is OIF's own
code at its own address.

## What it does not do

- Native assets only: XLM on Stellar testnet, ETH on Base Sepolia. Issued assets and more networks are a table entry and
  an adapter each.
- The court checks a payment, not why it was made. An order that asks for the wrong thing is the user's mistake.
- Test money only.
