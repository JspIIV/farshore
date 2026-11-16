import { Contract, AbiCoder, parseUnits, formatUnits } from 'ethers';
import fs from 'fs';
import { key } from './keys.mjs';
import { client, reader } from './net.mjs';
import { ChainProvider } from '../lib/rpc.mjs';
import { stellarKey, pay, recipientOf } from '../lib/stellar.mjs';
import { output, order, orderTuple, outputTuple, ident } from '../lib/oif.mjs';
const dep = JSON.parse(fs.readFileSync('deployment.json', 'utf8'));
const abi = (n) => JSON.parse(fs.readFileSync('evm/abi/' + n + '.json', 'utf8'));
const p = new ChainProvider();
const user = (await key('padv', process.env.PADV)).connect(p), solver = (await key('ppub', process.env.PPUB)).connect(p), stranger = (await key('pchg', process.env.PCHG)).connect(p);
const usd = new Contract(dep.test_usd.address, abi('TestUSD'), user);
const settler = new Contract(dep.input_settler.address, abi('InputSettlerEscrow'), user);
const oracle = new Contract(dep.oracle.address, abi('FarshoreOracle'), solver);
const sUser = await stellarKey('user'), sSolver = await stellarKey('solver');
const now = (await p.getBlock('latest')).timestamp;
await (await usd.mint(user.address, parseUnits('100', 18))).wait();
await (await usd.approve(dep.input_settler.address, parseUnits('100', 18))).wait();
const out = output({ oracle: dep.oracle.address, network: 'stellar-testnet', asset: 'XLM', amount: 25_000_000n, recipient: recipientOf(sUser.publicKey()) });
const o = order({ user: user.address, nonce: BigInt(now), inputToken: dep.test_usd.address, inputAmount: parseUnits('25', 18), oracle: dep.oracle.address, outputs: [out], fillDeadline: now + 3600, expires: now + 7200 });
const orderId = await settler.orderIdentifier(orderTuple(o));
await (await settler.open(orderTuple(o))).wait();
console.log('order', orderId, 'opened; user tUSD', formatUnits(await usd.balanceOf(user.address), 18));
const memo = await oracle.memoFor(orderId, ident(solver.address));
const tx = await pay(sSolver, sUser.publicKey(), 25_000_000n, memo);
console.log('stellar payment', tx);
const ctx = await oracle.claim(orderId, outputTuple(out), '0x' + tx); const rc = await ctx.wait();
const claimId = oracle.interface.parseLog(rc.logs.find((l) => l.address.toLowerCase() === dep.oracle.address.toLowerCase())).args.claimId;
console.log('claim', claimId);
fs.writeFileSync('results/smoke.json', JSON.stringify({ order: o, orderId, claimId, tx }, (k, v) => typeof v === 'bigint' ? v.toString() : v, 2));
const c = client(stranger.privateKey);
const gasLimit = 400000n, maxGasPrice = 1000000000n, budget = gasLimit * maxGasPrice;
const feeParams = AbiCoder.defaultAbiCoder().encode(['uint256', 'uint256'], [gasLimit, maxGasPrice]);
const messageAllocations = [{ messageType: 0, onAcceptance: false, recipient: dep.oracle.address, budget, feeParams }];
const e = await c.estimateTransactionFees({ totalMessageFees: budget, messageAllocations });
const vh = await c.writeContract({ address: dep.court.address, functionName: 'verify', args: [claimId], value: 0n, fees: { distribution: e.distribution, feeValue: e.feeValue, messageAllocations } });
console.log('court round', vh);
const r = reader(); let t;
for (let i = 0; i < 120; i++) { await new Promise((s) => setTimeout(s, 10000)); t = await r.getTransaction({ hash: vh }); if (['FINALIZED', 'UNDETERMINED', 'CANCELED'].includes(t.statusName)) break; }
console.log('round', t.statusName, t.txExecutionResultName, await r.readContract({ address: dep.court.address, functionName: 'ruling', args: [claimId] }));
let f; for (let i = 0; i < 40; i++) { f = await oracle.claimFacts(claimId); if (Number(f[0]) >= 2) break; await new Promise((s) => setTimeout(s, 5000)); }
console.log('oracle status', String(f[0]), 'filledAt', String(f[7]));
if (Number(f[0]) === 2) {
  const s2 = new Contract(dep.input_settler.address, abi('InputSettlerEscrow'), solver);
  const before = await usd.balanceOf(solver.address);
  await (await s2.finalise(orderTuple(o), [[Number(f[7]), ident(solver.address)]], ident(solver.address), '0x')).wait();
  console.log('finalised: solver +', formatUnits((await usd.balanceOf(solver.address)) - before, 18), 'tUSD');
}
process.exit(0);
