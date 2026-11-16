// Deploys Farshore on GenLayer Bradbury:
//   GenLayer Chain (4221): OIF's InputSettlerEscrow (unmodified), the test dollar and the FarshoreOracle;
//   GenLayer (Intelligent Contract): the Farshore court, then named in the oracle once.
//
//   PCHG=... node scripts/deploy.mjs
import { ContractFactory, Contract } from 'ethers';
import fs from 'fs'; import path from 'path';
import { client, fees, EXPLORER } from './net.mjs';
import { key } from './keys.mjs';
import { compile, ROOT } from './compile.mjs';
import { ChainProvider } from '../lib/rpc.mjs';

const w = (await key('pchg', process.env.PCHG)).connect(new ChainProvider());
const OUT = path.join(ROOT, 'deployment.json');
const dep = fs.existsSync(OUT) ? JSON.parse(fs.readFileSync(OUT, 'utf8')) : { network: 'genlayer testnet bradbury phase 2', chain_id: 4221 };
const save = () => fs.writeFileSync(OUT, JSON.stringify(dep, null, 2));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function deploy(k, name, args = []) {
  if (dep[k]) return dep[k].address;
  const c = compile(name);
  fs.mkdirSync(path.join(ROOT, 'evm', 'abi'), { recursive: true });
  fs.writeFileSync(path.join(ROOT, 'evm', 'abi', name + '.json'), JSON.stringify(c.abi, null, 2));
  const contract = await new ContractFactory(c.abi, c.bytecode, w).deploy(...args);
  await contract.waitForDeployment();
  dep[k] = { address: await contract.getAddress(), tx: contract.deploymentTransaction().hash };
  dep.compiler = c.version; save();
  console.log('deployed', k.padEnd(14), dep[k].address);
  return dep[k].address;
}

console.log('deployer', w.address);
await deploy('input_settler', 'InputSettlerEscrow');
await deploy('test_usd', 'TestUSD');
const oracle = await deploy('oracle', 'FarshoreOracle');

if (!dep.court) {
  const c = client(w.privateKey);
  const code = fs.readFileSync(path.join(ROOT, 'contracts', 'farshore.py'), 'utf8');
  const hash = await c.deployContract({ code, args: [oracle], fees: await fees(c) });
  console.log('court tx', hash);
  for (let i = 0; i < 60; i++) {
    await sleep(10000);
    const tx = await c.getTransaction({ hash });
    if (['ACCEPTED', 'FINALIZED'].includes(tx.statusName)) {
      if (tx.txExecutionResultName !== 'FINISHED_WITH_RETURN') { console.error('deploy failed', tx.txExecutionResultName); process.exit(1); }
      dep.court = { address: tx.txDataDecoded?.contractAddress || tx.recipient, tx: hash, deployed_at: new Date().toISOString() };
      save(); break;
    }
  }
}
if (!dep.wired) {
  const o = new Contract(oracle, compile('FarshoreOracle').abi, w);
  await (await o.setCourt(dep.court.address)).wait();
  dep.wired = true; save();
}
console.log('FARSHORE court', dep.court?.address, EXPLORER + '/address/' + dep.court?.address, '| oracle', oracle);
process.exit(0);
