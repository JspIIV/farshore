// The Farshore proof, on GenLayer Bradbury. Real OIF orders on OIF's own input settler (unmodified), real payments on
// Stellar testnet and Base Sepolia, and real Internet Court rounds that read those networks and write the attestation.
//
//   A  Stellar 2.5 XLM. A thief claims the solver's payment as its own   -> NOT_FILLED (the memo names the solver)
//      the solver claims the same payment                                -> FILLED, the solver finalises on OIF
//   B  Base Sepolia 0.0001 ETH, paid and claimed by the solver            -> FILLED, the solver finalises on OIF
//   C  Stellar 3 XLM, the solver pays 2.9                                 -> NOT_FILLED, the user is refunded after expiry
//   F  a transaction hash that exists nowhere                             -> no change at first, NOT_FILLED after the grace
//
// Every refusal is checked with eth_call from the account that would try it. Balances are counted to the unit.
//
//   PADV=... PPUB=... PCHG=... node scripts/prove.mjs        (about 25 minutes: C waits for its expiry, F for the grace)
import { Contract, Interface, AbiCoder, JsonRpcProvider, parseUnits, formatUnits, hexlify, randomBytes, keccak256, solidityPacked } from 'ethers';
import fs from 'fs'; import path from 'path'; import { fileURLToPath } from 'url';
import { key } from './keys.mjs';
import { client, reader, EXPLORER } from './net.mjs';
import { ChainProvider } from '../lib/rpc.mjs';
import { stellarKey, pay, recipientOf } from '../lib/stellar.mjs';
import { output, order, orderTuple, outputTuple, ident } from '../lib/oif.mjs';

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const dep = JSON.parse(fs.readFileSync(path.join(ROOT, 'deployment.json'), 'utf8'));
const abi = (n) => JSON.parse(fs.readFileSync(path.join(ROOT, 'evm', 'abi', n + '.json'), 'utf8'));
const OUT = path.join(ROOT, 'results', 'proof.json');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const STATUS = ['None', 'Claimed', 'Filled', 'NotFilled'];

const p = new ChainProvider();
const user = (await key('padv', process.env.PADV)).connect(p);
const solver = (await key('ppub', process.env.PPUB)).connect(p);
const stranger = (await key('pchg', process.env.PCHG)).connect(p);
const base = new JsonRpcProvider('https://sepolia.base.org', 84532, { staticNetwork: true });
const sUser = await stellarKey('user'), sSolver = await stellarKey('solver');
const SETTLER_ABI = abi('InputSettlerEscrow'), ORACLE_ABI = abi('FarshoreOracle'), USD_ABI = abi('TestUSD');
const usd = new Contract(dep.test_usd.address, USD_ABI, user);
const settlerAs = (w) => new Contract(dep.input_settler.address, SETTLER_ABI, w);
const oracleAs = (w) => new Contract(dep.oracle.address, ORACLE_ABI, w);
const errs = new Interface([...SETTLER_ABI, ...ORACLE_ABI].filter((x) => x.type === 'error'));

const proof = { network: 'GenLayer Bradbury', started: new Date().toISOString(),
  contracts: { input_settler: dep.input_settler.address, oracle: dep.oracle.address, test_usd: dep.test_usd.address, court: dep.court.address },
  accounts: { user: user.address, solver: solver.address, stranger: stranger.address, stellar_user: sUser.publicKey(), stellar_solver: sSolver.publicKey() },
  orders: {}, checks: [] };
const save = () => fs.writeFileSync(OUT, JSON.stringify(proof, (k, v) => (typeof v === 'bigint' ? v.toString() : v), 2));
function check(name, ok, detail = {}) {
  proof.checks.push({ n: proof.checks.length + 1, name, ok: !!ok, ...detail });
  console.log(`${ok ? 'PASS' : 'FAIL'} ${String(proof.checks.length).padStart(2)} ${name}${detail.note ? '  (' + detail.note + ')' : ''}`);
  save();
}
async function refused(name, fn, expect) {
  try { await fn(); check(name, false, { note: 'it went through' }); }
  catch (e) {
    let why = e.reason || e.revert?.name || '';
    if (!why && e.data) { try { why = errs.parseError(e.data)?.name || ''; } catch {} }
    why = why || e.shortMessage || String(e.message).slice(0, 120);
    check(name, why.includes(expect), { note: 'reverted: ' + why });
  }
}
const now = async () => (await p.getBlock('latest')).timestamp;
async function waitUntil(t, label) { for (;;) { const n = await now(); if (n >= t) return; console.log(`  waiting ${t - n}s for ${label}`); await sleep(Math.min((t - n) * 1000 + 3000, 60000)); } }
const balances = async () => ({ user: await usd.balanceOf(user.address), solver: await usd.balanceOf(solver.address), settler: await usd.balanceOf(dep.input_settler.address) });
const fmt = (b) => Object.fromEntries(Object.entries(b).map(([k, v]) => [k, formatUnits(v, 18)]));

// ------------------------------------------------------------------ orders
await (await usd.mint(user.address, parseUnits('100', 18))).wait();
await (await usd.approve(dep.input_settler.address, parseUnits('100', 18))).wait();
const start = await balances();
proof.balances_start = fmt(start);
let nonce = BigInt(await now()) * 1000n;

async function open(tag, out, inputAmount, fillIn, expireIn) {
  const t0 = await now();
  const o = order({ user: user.address, nonce: nonce++, inputToken: dep.test_usd.address, inputAmount: parseUnits(String(inputAmount), 18), oracle: dep.oracle.address, outputs: [out], fillDeadline: t0 + fillIn, expires: t0 + expireIn });
  const orderId = await settlerAs(user).orderIdentifier(orderTuple(o));
  const tx = await settlerAs(user).open(orderTuple(o)); await tx.wait();
  proof.orders[tag] = { orderId, order: o, open_tx: tx.hash };
  return { o, orderId, out };
}
const stellarOut = (stroops) => output({ oracle: dep.oracle.address, network: 'stellar-testnet', asset: 'XLM', amount: stroops, recipient: recipientOf(sUser.publicKey()) });
const A = await open('A', stellarOut(25_000_000n), 25, 3600, 7200);
const B = await open('B', output({ oracle: dep.oracle.address, network: 'base-sepolia', asset: 'ETH', amount: 100_000_000_000_000n, recipient: ident(user.address) }), 10, 3600, 7200);
const C = await open('C', stellarOut(30_000_000n), 30, 600, 900);
check('Three OIF orders are open on the unmodified InputSettlerEscrow, their inputs held by it', (await usd.balanceOf(dep.input_settler.address)) - start.settler === parseUnits('65', 18), { tx: proof.orders.A.open_tx });

// ------------------------------------------------------------------ the far-chain payments
const solverId = ident(solver.address), strangerId = ident(stranger.address);
const memoA = await oracleAs(solver).memoFor(A.orderId, solverId);
const txA = await pay(sSolver, sUser.publicKey(), 25_000_000n, memoA);
const memoC = await oracleAs(solver).memoFor(C.orderId, solverId);
const txC = await pay(sSolver, sUser.publicKey(), 29_000_000n, memoC);                 // 2.9 XLM against an order for 3
const baseSolver = (await key('ppub', process.env.PPUB)).connect(base);
const sentB = await baseSolver.sendTransaction({ to: user.address, value: 100_000_000_000_000n, data: await oracleAs(solver).memoFor(B.orderId, solverId) });
await sentB.wait();
const txB = sentB.hash.slice(2);
Object.assign(proof.orders.A, { far_tx: txA, far: 'stellar-testnet' });
Object.assign(proof.orders.B, { far_tx: txB, far: 'base-sepolia' });
Object.assign(proof.orders.C, { far_tx: txC, far: 'stellar-testnet' });
check('The solver pays on Stellar testnet and Base Sepolia, each payment carrying memoFor(order, solver)', !!txA && !!txB && !!txC, { note: `stellar ${txA.slice(0, 10)}, base ${txB.slice(0, 10)}` });

// ------------------------------------------------------------------ claims
async function claim(w, x, tx) {
  const c = oracleAs(w);
  const id = await c.claimIdOf(x.orderId, outputTuple(x.out), '0x' + tx, ident(w.address));
  const r = await c.claim(x.orderId, outputTuple(x.out), '0x' + tx); await r.wait();
  return { id, tx: r.hash };
}
const thiefA = await claim(stranger, A, txA);                                        // a stranger copies the solver's payment
const honestB = await claim(solver, B, txB);
const shortC = await claim(solver, C, txC);
const fakeHash = hexlify(randomBytes(32)).slice(2);
const fakeF = await claim(solver, A, fakeHash);
proof.orders.A.thief_claim = thiefA; proof.orders.B.claim = honestB; proof.orders.C.claim = shortC; proof.orders.A.fake_claim = fakeF;
save();

await refused('A claim must name this oracle as the output\'s oracle and settler', () => oracleAs(solver).claim.staticCall(A.orderId, outputTuple({ ...A.out, oracle: ident(user.address) }), '0x' + txA), 'Farshore:not-this-oracle');
await refused('A claim cannot carry a callback the far chain would never run', () => oracleAs(solver).claim.staticCall(A.orderId, outputTuple({ ...A.out, callbackData: '0x01' }), '0x' + txA), 'Farshore:no-callback');
await refused('The same claim cannot be made twice', () => oracleAs(solver).claim.staticCall(B.orderId, outputTuple(B.out), '0x' + txB), 'Farshore:claimed');
await refused('Nobody but the court can settle a claim', () => oracleAs(stranger).settle.staticCall(honestB.id, true, 1, '0x' + '00'.repeat(32)), 'Farshore:only-court');
await refused('The solver cannot finalise on OIF before the court has proven the fill', () => settlerAs(solver).finalise.staticCall(orderTuple(B.o), [[1, solverId]], solverId, '0x'), 'NotProven');
await refused('The user cannot be refunded before the order expires', () => settlerAs(user).refund.staticCall(orderTuple(C.o)), 'TimestampNotPassed');

// ------------------------------------------------------------------ the court
async function ask(claimId, who) {
  const c = client(who.privateKey);
  const gasLimit = 400000n, maxGasPrice = 1000000000n, budget = gasLimit * maxGasPrice;
  const feeParams = AbiCoder.defaultAbiCoder().encode(['uint256', 'uint256'], [gasLimit, maxGasPrice]);
  const messageAllocations = [{ messageType: 0, onAcceptance: false, recipient: dep.oracle.address, budget, feeParams }];
  const e = await c.estimateTransactionFees({ totalMessageFees: budget, messageAllocations });
  return c.writeContract({ address: dep.court.address, functionName: 'verify', args: [claimId], value: 0n, fees: { distribution: e.distribution, feeValue: e.feeValue, messageAllocations } });
}
async function settled(claimId, round, expectChange = true) {
  const r = reader(); let t;
  for (let i = 0; i < 120; i++) { await sleep(10000); t = await r.getTransaction({ hash: round }); if (['FINALIZED', 'UNDETERMINED', 'CANCELED'].includes(t.statusName)) break; }
  let f;
  for (let i = 0; i < (expectChange ? 40 : 2); i++) { f = await oracleAs(p).claimFacts(claimId); if (Number(f[0]) >= 2) break; await sleep(5000); }
  const ruling = JSON.parse(await r.readContract({ address: dep.court.address, functionName: 'ruling', args: [claimId] }));
  return { round: t?.statusName, status: STATUS[Number(f[0])], filledAt: Number(f[7]), ruling, times: await oracleAs(p).claimTimes(claimId) };
}
// Each claim is put to the court by somebody who is not its claimer.
const rounds = { thiefA: await ask(thiefA.id, user), B: await ask(honestB.id, stranger), C: await ask(shortC.id, stranger), F: await ask(fakeF.id, stranger) };
console.log('court rounds', rounds);
const [rThief, rB, rC, rF] = await Promise.all([settled(thiefA.id, rounds.thiefA), settled(honestB.id, rounds.B), settled(shortC.id, rounds.C), settled(fakeF.id, rounds.F, false)]);
Object.assign(proof.orders.A, { thief_round: rounds.thiefA, thief_verdict: rThief.ruling.verdict, thief_why: rThief.ruling.why });
Object.assign(proof.orders.B, { round: rounds.B, verdict: rB.ruling.verdict, why: rB.ruling.why });
Object.assign(proof.orders.C, { round: rounds.C, verdict: rC.ruling.verdict, why: rC.ruling.why });
check('A: a stranger claiming the solver\'s Stellar payment as its own gets NOT_FILLED: the memo names the solver', rThief.round === 'FINALIZED' && rThief.status === 'NotFilled', { note: rThief.ruling.why, tx: rounds.thiefA, gl: true });
check('B: the Base Sepolia payment is FILLED, read by the court from Blockscout', rB.round === 'FINALIZED' && rB.status === 'Filled' && rB.filledAt > 0, { note: rB.ruling.why, tx: rounds.B, gl: true });
check('C: a payment of 2.9 XLM against an order for 3 is NOT_FILLED', rC.round === 'FINALIZED' && rC.status === 'NotFilled', { note: rC.ruling.why, tx: rounds.C, gl: true });
check('F: a transaction the network does not show changes nothing inside the grace period', rF.status === 'Claimed' && rF.ruling.exists === false, { note: rF.round, tx: rounds.F, gl: true });

// A, the honest claim on the same payment the thief failed with
const honestA = await claim(solver, A, txA);
proof.orders.A.claim = honestA;
const roundA = await ask(honestA.id, stranger);
const rA = await settled(honestA.id, roundA);
Object.assign(proof.orders.A, { round: roundA, verdict: rA.ruling.verdict, why: rA.ruling.why });
check('A: the solver claims the same payment and it is FILLED, read by the court from Horizon', rA.round === 'FINALIZED' && rA.status === 'Filled', { note: rA.ruling.why, tx: roundA, gl: true });
const fillA = keccak256(solidityPacked(['bytes32', 'bytes32', 'uint32', 'bytes32', 'uint256', 'bytes32', 'uint16', 'uint16'], [solverId, A.orderId, rA.filledAt, A.out.token, A.out.amount, A.out.recipient, 0, 0]));
check('The court\'s own call wrote OIF\'s attestation: isProven holds for A\'s fill description', await oracleAs(p).isProven(4221, ident(dep.oracle.address), ident(dep.oracle.address), fillA));
await refused('A filled payment cannot be claimed again, for any order', () => oracleAs(solver).claim.staticCall(C.orderId, outputTuple(C.out), '0x' + txA), 'Farshore:tx-used');
await refused('The court cannot settle a claim twice', () => oracleAs(p).settle.staticCall(honestA.id, false, 0, '0x' + '00'.repeat(32), { from: dep.court.address }), 'Farshore:not-claimed');

// ------------------------------------------------------------------ finalise on OIF
await refused('Finalising with a timestamp other than the proven one fails', () => settlerAs(solver).finalise.staticCall(orderTuple(A.o), [[rA.filledAt + 1, solverId]], solverId, '0x'), 'NotProven');
await refused('Nobody but the solver can finalise its fill', () => settlerAs(stranger).finalise.staticCall(orderTuple(A.o), [[rA.filledAt, solverId]], strangerId, '0x'), 'NotOrderOwner');
for (const [tag, x, r] of [['A', A, rA], ['B', B, rB]]) {
  const before = await usd.balanceOf(solver.address);
  const fin = await settlerAs(solver).finalise(orderTuple(x.o), [[r.filledAt, solverId]], solverId, '0x'); await fin.wait();
  proof.orders[tag].finalise_tx = fin.hash;
  check(`${tag}: the solver finalises on OIF's settler and is paid the order's inputs`, (await usd.balanceOf(solver.address)) - before === x.o.inputs[0][1], { tx: fin.hash, note: '+' + formatUnits(x.o.inputs[0][1], 18) + ' tUSD' });
}
await refused('A finalised order cannot be finalised again', () => settlerAs(solver).finalise.staticCall(orderTuple(A.o), [[rA.filledAt, solverId]], solverId, '0x'), 'InvalidOrderStatus');

// ------------------------------------------------------------------ C refunded, F after the grace
await waitUntil(C.o.expires + 1, 'C to expire');
const ref = await settlerAs(stranger).refund(orderTuple(C.o)); await ref.wait();
proof.orders.C.refund_tx = ref.hash;
check('C: after expiry anybody triggers OIF\'s refund and the inputs go back to the user', true, { tx: ref.hash });
const claimedAt = Number(rF.times[0]);
await waitUntil(claimedAt + 15 * 60 + 5, 'F\'s grace period');
const roundF2 = await ask(fakeF.id, stranger);
const rF2 = await settled(fakeF.id, roundF2);
Object.assign(proof.orders.A, { fake_round: roundF2, fake_verdict: rF2.ruling.verdict, fake_why: rF2.ruling.why });
check('F: after the grace, a transaction the network still does not show is NOT_FILLED', rF2.round === 'FINALIZED' && rF2.status === 'NotFilled', { note: rF2.ruling.why, tx: roundF2, gl: true });

// ------------------------------------------------------------------ the money, to the unit
const end = await balances();
proof.balances_end = fmt(end);
const got = { user: end.user - start.user, solver: end.solver - start.solver, settler: end.settler - start.settler };
proof.balance_changes = fmt(got);
check('The money, to the unit: the user paid 35 tUSD for the two filled orders and got the unfilled one back; the solver got exactly 35; nothing is left in the settler',
  got.user === -parseUnits('35', 18) && got.solver === parseUnits('35', 18) && got.settler === 0n, { note: JSON.stringify(fmt(got)) });

proof.finished = new Date().toISOString();
proof.passed = proof.checks.filter((c) => c.ok).length;
proof.total = proof.checks.length;
save();
console.log(`\n${proof.passed}/${proof.total} checks passed. ${OUT}`);
console.log('court', EXPLORER + '/address/' + dep.court.address);
process.exit(proof.passed === proof.total ? 0 : 1);
