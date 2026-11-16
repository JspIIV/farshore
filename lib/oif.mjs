// Open Intents Framework orders, as Farshore uses them: the StandardOrder a user opens on OIF's input settler, the
// output that names the FarshoreOracle, and the identifiers the oracle and the settler compute.
import { AbiCoder, getAddress, zeroPadValue, toUtf8Bytes, hexlify, concat } from 'ethers';

export const CHAIN_ID = 4221;
export const ident = (address) => zeroPadValue(getAddress(address), 32).toLowerCase();

/// The output token: the network in the high 16 bytes and the asset in the low 16, both ASCII.
export function tokenTag(network, asset) {
  const pad = (s) => { const b = toUtf8Bytes(s); if (b.length > 16) throw new Error('tag too long: ' + s); return hexlify(concat([b, new Uint8Array(16 - b.length)])); };
  return concat([pad(network), pad(asset)]).toLowerCase();
}

export function output({ oracle, network, asset, amount, recipient }) {
  return { oracle: ident(oracle), settler: ident(oracle), chainId: CHAIN_ID, token: tokenTag(network, asset), amount: BigInt(amount), recipient, callbackData: '0x', context: '0x' };
}

export function order({ user, nonce, inputToken, inputAmount, oracle, outputs, fillDeadline, expires }) {
  return { user, nonce: BigInt(nonce), originChainId: CHAIN_ID, expires, fillDeadline, inputOracle: oracle, inputs: [[BigInt(inputToken), BigInt(inputAmount)]], outputs };
}
export const outputTuple = (o) => [o.oracle, o.settler, o.chainId, o.token, o.amount, o.recipient, o.callbackData, o.context];
export const orderTuple = (o) => [o.user, o.nonce, o.originChainId, o.expires, o.fillDeadline, o.inputOracle, o.inputs, o.outputs.map(outputTuple)];
