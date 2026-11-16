// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import { ERC20 } from "openzeppelin/token/ERC20/ERC20.sol";

/// @title TestUSD
/// @notice The test dollar an order locks as its input. Anybody may mint up to 1,000 at a time. Testnet only.
contract TestUSD is ERC20 {
    constructor() ERC20("Test USD", "tUSD") {}

    function mint(address to, uint256 amount) external {
        require(amount <= 1_000e18, "TestUSD:too-much");
        _mint(to, amount);
    }
}
