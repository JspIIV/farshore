// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import { BaseInputOracle } from "oif/oracles/BaseInputOracle.sol";
import { LibAddress } from "oif/libs/LibAddress.sol";
import { MandateOutput, MandateOutputEncodingLib } from "oif/libs/MandateOutputEncodingLib.sol";

/// @title FarshoreOracle
/// @notice An Open Intents Framework input oracle for outputs on chains no bridge reaches, with Internet Court as the
///         party that checks them.
///
///         An OIF order locks the user's inputs in an input settler on this chain and names, for each output, the
///         oracle that will prove it was filled. OIF's own BitcoinOracle shows the pattern for a chain with no messaging
///         layer: the output names this chain and the oracle itself, and the oracle writes the fill into its own
///         attestations once it has checked the payment. FarshoreOracle does the same for any chain with a public
///         explorer API, and the check is made by Internet Court on GenLayer instead of a light client:
///
///         - An output is `{ oracle: this, settler: this, chainId: this chain, token: network and asset, amount,
///           recipient }`. `token` holds two ASCII fields, the network in its high 16 bytes and the asset in its low 16
///           (for example "stellar-testnet" and "XLM"). `recipient` is the raw 32-byte account on that network.
///         - The solver pays on the far chain with a memo that binds the payment to the order and to itself:
///           `memoFor(orderId, solver)`. A payment copied by somebody else carries the wrong memo for them.
///         - The solver claims the fill here, naming the far-chain transaction.
///         - Anybody asks the court. GenLayer validators read the transaction from the network's public API, chosen
///           by the court from the output's network, never from the solver, and agree whether it pays exactly that
///           amount of that asset to that recipient with that memo. FILLED writes the OIF attestation, with the
///           transaction's own timestamp; NOT_FILLED records the refusal.
///         - The solver then finalises on OIF's unmodified input settler and is paid. An order nobody fills is
///           refunded by the settler after it expires.
///
///         No relayer, no owner. The deployer's only power is to name the court once.
contract FarshoreOracle is BaseInputOracle {
    using LibAddress for address;

    enum Status { None, Claimed, Filled, NotFilled }

    struct Claim {
        Status status;
        uint32 filledAt;
        uint48 claimedAt;
        bytes32 orderId;
        bytes32 solver;
        bytes32 txHash;
        bytes32 token;
        uint256 amount;
        bytes32 recipient;
        bytes32 reason;
    }

    address public immutable deployer;
    address public court;

    mapping(bytes32 claimId => Claim) internal claims;
    /// The claim a far-chain transaction filled. One transaction fills one output, once.
    mapping(bytes32 txHash => bytes32 claimId) public filledBy;
    bytes32[] public claimIds;

    event CourtSet(address court);
    event Claimed(bytes32 indexed claimId, bytes32 indexed orderId, bytes32 solver, bytes32 txHash, bytes32 token, uint256 amount, bytes32 recipient);
    event Settled(bytes32 indexed claimId, bool filled, uint32 filledAt, bytes32 reason);
    event OutputFilled(bytes32 indexed orderId, bytes32 solver, uint32 timestamp, bytes32 token, uint256 amount, bytes32 recipient);

    constructor() {
        deployer = msg.sender;
    }

    function setCourt(address court_) external {
        require(msg.sender == deployer, "Farshore:only-deployer");
        require(court == address(0) && court_ != address(0), "Farshore:court-set");
        court = court_;
        emit CourtSet(court_);
    }

    /// The memo a solver puts on its far-chain payment: Stellar `memo_hash`, or the input data of an EVM transfer.
    function memoFor(bytes32 orderId, bytes32 solver) public pure returns (bytes32) {
        return keccak256(abi.encode(orderId, solver));
    }

    function claimIdOf(bytes32 orderId, MandateOutput calldata output, bytes32 txHash, bytes32 solver) public pure returns (bytes32) {
        return keccak256(abi.encode(orderId, MandateOutputEncodingLib.getMandateOutputHash(output), txHash, solver));
    }

    /// A solver claims that `txHash` on the output's network fills `output` of order `orderId`.
    function claim(bytes32 orderId, MandateOutput calldata output, bytes32 txHash) external returns (bytes32 claimId) {
        bytes32 self = address(this).toIdentifier();
        require(output.oracle == self && output.settler == self && output.chainId == block.chainid, "Farshore:not-this-oracle");
        require(output.callbackData.length == 0 && output.context.length == 0, "Farshore:no-callback");
        require(output.amount > 0 && output.recipient != bytes32(0) && output.token != bytes32(0), "Farshore:bad-output");
        require(txHash != bytes32(0), "Farshore:no-tx");
        require(filledBy[txHash] == bytes32(0), "Farshore:tx-used");
        bytes32 solver = msg.sender.toIdentifier();
        claimId = claimIdOf(orderId, output, txHash, solver);
        require(claims[claimId].status == Status.None, "Farshore:claimed");
        claims[claimId] = Claim({
            status: Status.Claimed, filledAt: 0, claimedAt: uint48(block.timestamp), orderId: orderId, solver: solver,
            txHash: txHash, token: output.token, amount: output.amount, recipient: output.recipient, reason: bytes32(0)
        });
        claimIds.push(claimId);
        emit Claimed(claimId, orderId, solver, txHash, output.token, output.amount, output.recipient);
    }

    /// Internet Court's ruling on a claim, sent by the court itself when its verdict is final.
    function settle(bytes32 claimId, bool filled, uint32 filledAt, bytes32 reason) external {
        require(msg.sender == court, "Farshore:only-court");
        Claim storage c = claims[claimId];
        require(c.status == Status.Claimed, "Farshore:not-claimed");
        c.reason = reason;
        if (filled && filledBy[c.txHash] == bytes32(0)) {
            c.status = Status.Filled;
            c.filledAt = filledAt;
            filledBy[c.txHash] = claimId;
            bytes32 self = address(this).toIdentifier();
            bytes32 fill = keccak256(MandateOutputEncodingLib.encodeFillDescriptionMemory(
                c.solver, c.orderId, filledAt, c.token, c.amount, c.recipient, hex"", hex""
            ));
            _attestations[block.chainid][self][self][fill] = true;
            emit OutputProven(block.chainid, self, self, fill);
            emit OutputFilled(c.orderId, c.solver, filledAt, c.token, c.amount, c.recipient);
        } else {
            c.status = Status.NotFilled;
        }
        emit Settled(claimId, c.status == Status.Filled, c.filledAt, reason);
    }

    // ------------------------------------------------------------------ reads, flat for any caller

    function claimCount() external view returns (uint256) {
        return claimIds.length;
    }

    function claimFacts(bytes32 claimId)
        external
        view
        returns (uint8 status, bytes32 orderId, bytes32 solver, bytes32 txHash, bytes32 token, uint256 amount, bytes32 recipient, uint32 filledAt)
    {
        Claim storage c = claims[claimId];
        return (uint8(c.status), c.orderId, c.solver, c.txHash, c.token, c.amount, c.recipient, c.filledAt);
    }

    function claimTimes(bytes32 claimId) external view returns (uint48 claimedAt, bytes32 reason) {
        Claim storage c = claims[claimId];
        return (c.claimedAt, c.reason);
    }
}
