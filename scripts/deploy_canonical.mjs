// Deploys OIF's InputSettlerEscrow on GenLayer Chain at its canonical address, 0x1CC9260E285C2C8AC8D2E7102F3978056Ec1d0a8,
// the address it has on Ethereum, Base, Arbitrum and every chain in OIF's DEPLOYMENTS.md: the same init code, rebuilt
// here from source (scripts/canonical.mjs), sent through the same deterministic deployment proxy with the same salt.
//   PCHG=... node scripts/deploy_canonical.mjs
import { concat, ZeroHash } from 'ethers';
import fs from 'fs'; import path from 'path';
import { key } from './keys.mjs';
import { ChainProvider } from '../lib/rpc.mjs';
import { canonicalInitCode, FACTORY, CANONICAL } from './canonical.mjs';
import { ROOT } from './compile.mjs';
const p = new ChainProvider();
const w = (await key('pchg', process.env.PCHG)).connect(p);
const { initcode, deployed, abi } = canonicalInitCode();
if ((await p.getCode(CANONICAL)) === '0x') {
  const tx = await w.sendTransaction({ to: FACTORY, data: concat([ZeroHash, initcode]), gasLimit: 8_000_000n });
  const r = await tx.wait();
  console.log('deployed through the proxy', tx.hash, 'gas', r.gasUsed.toString());
}
const live = (await p.getCode(CANONICAL)).toLowerCase();
console.log('code at', CANONICAL, (live.length - 2) / 2, 'bytes, equals the rebuilt runtime code:', live === deployed.toLowerCase());
const OUT = path.join(ROOT, 'deployment.json');
const dep = JSON.parse(fs.readFileSync(OUT, 'utf8'));
dep.input_settler = { address: CANONICAL, via: FACTORY, salt: ZeroHash, oif_commit: 'b260675' };
fs.writeFileSync(OUT, JSON.stringify(dep, null, 2));
fs.writeFileSync(path.join(ROOT, 'evm', 'abi', 'InputSettlerEscrow.json'), JSON.stringify(abi, null, 2));
