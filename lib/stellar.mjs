// Stellar testnet, as Farshore's solver and its tests reach it. The keys are local test keys, kept outside the repo.
import { Keypair, Horizon, TransactionBuilder, Networks, Operation, Asset, Memo, BASE_FEE } from '@stellar/stellar-sdk';
import fs from 'fs'; import os from 'os'; import path from 'path';

export const HORIZON = 'https://horizon-testnet.stellar.org';
export const server = new Horizon.Server(HORIZON);
const KS = path.join(os.homedir(), '.genlayer', 'keystores');

/// A named local testnet keypair, created and funded by friendbot on first use.
export async function stellarKey(name) {
  const file = path.join(KS, 'stellar_' + name + '.secret');
  let kp;
  if (fs.existsSync(file)) kp = Keypair.fromSecret(fs.readFileSync(file, 'utf8').trim());
  else { kp = Keypair.random(); fs.writeFileSync(file, kp.secret()); }
  try { await server.loadAccount(kp.publicKey()); }
  catch { const r = await fetch('https://friendbot.stellar.org?addr=' + kp.publicKey()); if (!r.ok) throw new Error('friendbot ' + r.status); }
  return kp;
}

/// Raw 32-byte ed25519 public key, as an OIF output recipient.
export const recipientOf = (publicKey) => '0x' + Buffer.from(Keypair.fromPublicKey(publicKey).rawPublicKey()).toString('hex');

/// Pay `stroops` of XLM to `to`, with a 32-byte hash memo. Returns the transaction hash (hex, no 0x).
export async function pay(from, to, stroops, memoHex) {
  const account = await server.loadAccount(from.publicKey());
  const amount = (BigInt(stroops) / 10_000_000n).toString() + '.' + (BigInt(stroops) % 10_000_000n).toString().padStart(7, '0');
  const tx = new TransactionBuilder(account, { fee: BASE_FEE, networkPassphrase: Networks.TESTNET })
    .addOperation(Operation.payment({ destination: to, asset: Asset.native(), amount }))
    .addMemo(Memo.hash(Buffer.from(memoHex.replace(/^0x/, ''), 'hex')))
    .setTimeout(120).build();
  tx.sign(from);
  const res = await server.submitTransaction(tx);
  return res.hash;
}
