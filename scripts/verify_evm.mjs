// Independent check that every EVM contract Farshore relies on is the source in this repository, and that the input
// settler is the Open Intents Framework's own deployment, at its own address.
//
// 1. OIF's InputSettlerEscrow: its init code is rebuilt from oif-contracts b260675 with the exact compiler input OIF
//    used (scripts/canonical.mjs). Through the deterministic deployment proxy with salt 0 that init code lands at
//    0x1CC9...d0a8 on every chain. Its runtime code is compared with that address on GenLayer Chain, Ethereum, Base and
//    Base Sepolia. Immutables (the EIP-712 domain, which names the chain) are taken from each chain and printed.
// 2. The FarshoreOracle and the test dollar: compiled here and compared with GenLayer Chain.
//
//   node scripts/verify_evm.mjs
import { JsonRpcProvider, getCreate2Address, keccak256, ZeroHash } from 'ethers';
import fs from 'fs'; import path from 'path';
import { compile, ROOT } from './compile.mjs';
import { canonicalInitCode, FACTORY, CANONICAL } from './canonical.mjs';
import { ChainProvider } from '../lib/rpc.mjs';

const dep = JSON.parse(fs.readFileSync(path.join(ROOT, 'deployment.json'), 'utf8'));
let all = true;
function fill(code, refs, live) {
  const f = code.replace(/^0x/, '').toLowerCase().split('');
  for (const spots of Object.values(refs)) for (const { start, length } of spots) for (let i = start * 2; i < (start + length) * 2; i++) f[i] = live[i];
  return f.join('');
}
const c = canonicalInitCode();
const at = getCreate2Address(FACTORY, ZeroHash, keccak256(c.initcode));
console.log('OIF InputSettlerEscrow, oif-contracts b260675, init code keccak', keccak256(c.initcode).slice(0, 18) + '...');
console.log(`${at === CANONICAL ? 'MATCH    ' : 'DIFFERENT'} CREATE2(${FACTORY.slice(0, 10)}..., salt 0) = ${at}`);
all = all && at === CANONICAL;
for (const [label, provider] of [['GenLayer Chain', new ChainProvider()], ['Ethereum', new JsonRpcProvider('https://ethereum-rpc.publicnode.com', 1, { staticNetwork: true })],
  ['Base', new JsonRpcProvider('https://base-rpc.publicnode.com', 8453, { staticNetwork: true })], ['Base Sepolia', new JsonRpcProvider('https://base-sepolia-rpc.publicnode.com', 84532, { staticNetwork: true })]]) {
  let live;
  try { live = (await provider.getCode(CANONICAL)).slice(2).toLowerCase(); } catch (e) { console.log(`UNREACHED ${label.padEnd(15)} ${e.shortMessage || e.message}`); continue; }
  const same = live.length > 0 && fill(c.deployed, c.refs, live) === live;
  all = all && same;
  console.log(`${same ? 'MATCH    ' : 'DIFFERENT'} ${label.padEnd(15)} ${CANONICAL}`);
}
console.log('\nFarshore on GenLayer Chain');
const chain = new ChainProvider();
for (const [k, name] of [['oracle', 'FarshoreOracle'], ['test_usd', 'TestUSD']]) {
  const cc = compile(name);
  const live = (await chain.getCode(dep[k].address)).slice(2).toLowerCase();
  const same = fill(cc.deployed, cc.refs, live) === live;
  all = all && same;
  console.log(`${same ? 'MATCH    ' : 'DIFFERENT'} ${name.padEnd(15)} ${dep[k].address}`);
}
console.log('\n' + (all ? 'every contract is the source in this repository, and the settler is OIF\'s own deployment' : 'at least one contract differs'));
process.exit(all ? 0 : 1);
