// Rebuilds OIF's canonical InputSettlerEscrow init code from source: commit b260675 of oif-contracts, its pinned
// OpenZeppelin and Permit2, and the exact compiler input Blockscout shows for 0x1CC9...d0a8 (paths, remappings,
// metadata). Then the CREATE2 address through the deterministic deployment proxy with salt 0.
import { createRequire } from 'module'; import { execFileSync } from 'child_process';
import fs from 'fs'; import path from 'path';
import { getCreate2Address, keccak256, ZeroHash } from 'ethers';
const require = createRequire(import.meta.url);
const solc = require('solc/wrapper')(require(path.resolve('tools/soljson-v0.8.30.cjs')));
const OIF = path.resolve('vendor/oif-contracts'), COMMIT = 'b260675';
const REMAPPINGS = ['ds-test/=lib/forge-std/lib/ds-test/src/', 'forge-std/=lib/forge-std/src/', 'the-compact/=lib/the-compact/', 'permit2/=lib/permit2/', 'openzeppelin/=lib/openzeppelin-contracts/contracts/', '@openzeppelin/=lib/openzeppelin-contracts/', '@eth-optimism/=lib/@eth-optimism/', 'broadcaster/=lib/broadcaster/src/contracts/', 'broadcaster-test/=lib/broadcaster/test/', '@arbitrum/nitro-contracts/=lib/broadcaster/node_modules/@arbitrum/nitro-contracts/', 'block-hash-pusher/=lib/broadcaster/lib/block-hash-pusher/', 'erc4626-tests/=lib/openzeppelin-contracts/lib/erc4626-tests/', 'forge-gas-snapshot/=lib/permit2/lib/forge-gas-snapshot/src/', 'halmos-cheatcodes/=lib/openzeppelin-contracts/lib/halmos-cheatcodes/src/', 'openzeppelin-contracts/=lib/openzeppelin-contracts/', 'solady/=lib/the-compact/lib/solady/src/', 'soledge/=lib/the-compact/lib/soledge/src/', 'solmate/=lib/permit2/lib/solmate/'];
export const FACTORY = '0x4e59b44847b379578588920cA78FbF26c0B4956C';
export const CANONICAL = '0x1CC9260E285C2C8AC8D2E7102F3978056Ec1d0a8';
const read = (p) => {
  if (p.startsWith('lib/')) { const f = path.join(OIF, p); return fs.existsSync(f) ? fs.readFileSync(f, 'utf8').replace(/\r\n/g, '\n') : null; }
  try { return execFileSync('git', ['-C', OIF, 'show', COMMIT + ':' + p], { stdio: ['ignore', 'pipe', 'ignore'] }).toString(); } catch { return null; }
};
export function canonicalInitCode() {
  const file = 'src/input/escrow/InputSettlerEscrow.sol';
  const out = JSON.parse(solc.compile(JSON.stringify({ language: 'Solidity', sources: { [file]: { content: read(file) } },
    settings: { viaIR: true, optimizer: { enabled: true, runs: 100_000_000 }, evmVersion: 'cancun', remappings: REMAPPINGS, libraries: {},
      metadata: { appendCBOR: true, bytecodeHash: 'ipfs', useLiteralContent: false }, outputSelection: { [file]: { InputSettlerEscrow: ['abi', 'evm.bytecode.object', 'evm.deployedBytecode.object', 'evm.deployedBytecode.immutableReferences'] } } } }),
    { import: (p) => { const c = read(p); return c == null ? { error: 'not found ' + p } : { contents: c }; } }));
  const errs = (out.errors || []).filter((e) => e.severity === 'error');
  if (errs.length) throw new Error(errs.map((e) => e.formattedMessage).join('\n'));
  const c = out.contracts[file].InputSettlerEscrow;
  return { initcode: '0x' + c.evm.bytecode.object, deployed: '0x' + c.evm.deployedBytecode.object, refs: c.evm.deployedBytecode.immutableReferences || {}, abi: c.abi };
}
if (path.basename(process.argv[1] || '') === 'canonical.mjs') {
  const { initcode } = canonicalInitCode();
  console.log('initcode bytes', (initcode.length - 2) / 2, 'keccak', keccak256(initcode));
  console.log('create2 address', getCreate2Address(FACTORY, ZeroHash, keccak256(initcode)), 'canonical', CANONICAL);
  const b = await (await fetch('https://base-sepolia.blockscout.com/api/v2/smart-contracts/' + CANONICAL)).json();
  console.log('equals the creation code Blockscout shows on Base Sepolia:', b.creation_bytecode?.toLowerCase() === initcode.toLowerCase());
}
