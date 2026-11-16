// Independent check of the proof, with no keys. Anyone can run it from a fresh clone:
//
//   node scripts/verify.mjs
//
// It trusts nothing this repository says about the outcome. It compares the court's deployed source with
// contracts/farshore.py byte for byte; asks GenLayer for every court round in results/proof.json; asks Stellar testnet
// and Base Sepolia, through their own public APIs, for every far-chain payment; asks GenLayer Chain for every claim's
// final state on the oracle and every order's final status on OIF's settler.
import crypto from 'crypto'; import fs from 'fs'; import path from 'path'; import url from 'url';
import { Contract } from 'ethers';
import { reader, EXPLORER } from './net.mjs';
import { ChainProvider } from '../lib/rpc.mjs';

const ROOT = path.join(path.dirname(url.fileURLToPath(import.meta.url)), '..');
const proof = JSON.parse(fs.readFileSync(path.join(ROOT, 'results', 'proof.json'), 'utf8'));
const dep = JSON.parse(fs.readFileSync(path.join(ROOT, 'deployment.json'), 'utf8'));
const abi = (n) => JSON.parse(fs.readFileSync(path.join(ROOT, 'evm', 'abi', n + '.json'), 'utf8'));
const c = reader();
const chain = new ChainProvider();
const oracle = new Contract(dep.oracle.address, abi('FarshoreOracle'), chain);
const settler = new Contract(dep.input_settler.address, ['function orderStatus(bytes32) view returns (uint8)'], chain);
const sha = (t) => crypto.createHash('sha256').update(t).digest('hex');
const STATUS = ['None', 'Claimed', 'Filled', 'NotFilled'];
const ORDER = ['None', 'Deposited', 'Claimed', 'Refunded'];
let ok = true;

// 1. the court's source
const local = fs.readFileSync(path.join(ROOT, 'contracts', 'farshore.py'), 'utf8').replace(/\r\n/g, '\n');
const raw = await c.getContractCode(dep.court.address);
const onChain = (typeof raw === 'string' ? raw : Buffer.from(raw).toString('utf8')).replace(/\r\n/g, '\n');
const same = onChain === local;
ok = ok && same;
console.log('court', dep.court.address, EXPLORER + '/address/' + dep.court.address);
console.log('  source local sha256', sha(local));
console.log('  source chain sha256', sha(onChain), same ? 'MATCH' : 'DIFFERENT');

// 2. the court's rounds, and what each claim ended as on the oracle
console.log('\ncourt rounds (GenLayer Bradbury) and claims (GenLayer Chain)');
const rounds = [];
for (const [tag, o] of Object.entries(proof.orders)) {
  if (o.thief_round) rounds.push([tag + ' thief', o.thief_round, o.thief_claim.id, 'NotFilled']);
  if (o.round) rounds.push([tag, o.round, o.claim.id, o.verdict === 'FILLED' ? 'Filled' : 'NotFilled']);
  if (o.fake_round) rounds.push([tag + ' fake', o.fake_round, o.fake_claim.id, 'NotFilled']);
}
for (const [label, round, claimId, want] of rounds) {
  const tx = await c.getTransaction({ hash: round });
  const st = STATUS[Number((await oracle.claimFacts(claimId))[0])];
  const good = tx.statusName === 'FINALIZED' && st === want;
  ok = ok && good;
  console.log(`  ${good ? 'OK ' : 'BAD'} ${label.padEnd(8)} ${tx.statusName.padEnd(10)} claim ${st.padEnd(9)} ${round}`);
}

// 3. the far-chain payments, from the networks themselves
console.log('\nfar-chain payments');
for (const [tag, o] of Object.entries(proof.orders)) {
  if (!o.far_tx) continue;
  let seen;
  if (o.far === 'stellar-testnet') {
    const t = await (await fetch('https://horizon-testnet.stellar.org/transactions/' + o.far_tx)).json();
    seen = t.successful === true && t.memo_type === 'hash';
  } else {
    const t = await (await fetch('https://base-sepolia.blockscout.com/api/v2/transactions/0x' + o.far_tx)).json();
    seen = t.status === 'ok' && t.result === 'success';
  }
  ok = ok && seen;
  console.log(`  ${seen ? 'OK ' : 'BAD'} ${tag} ${o.far.padEnd(16)} ${o.far_tx}`);
}

// 4. every order's end on OIF's settler
console.log('\norders on OIF\'s InputSettlerEscrow', dep.input_settler.address);
const WANT = { A: 'Claimed', B: 'Claimed', C: 'Refunded' };
for (const [tag, o] of Object.entries(proof.orders)) {
  const st = ORDER[Number(await settler.orderStatus(o.orderId))];
  const good = st === WANT[tag];
  ok = ok && good;
  console.log(`  ${good ? 'OK ' : 'BAD'} ${tag} ${st.padEnd(9)} ${o.orderId}`);
}
console.log('\n' + (ok ? 'everything in results/proof.json is what the chains say' : 'something differs from the chains'));
fs.writeFileSync(path.join(ROOT, 'results', 'finality.json'), JSON.stringify({ checked_at: new Date().toISOString(), court: dep.court.address, source_sha256: sha(local), source_matches_chain: same, all_ok: ok }, null, 2));
process.exit(ok ? 0 : 1);
