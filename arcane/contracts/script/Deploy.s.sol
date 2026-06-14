// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "../src/ArcaneSettlement.sol";
import "../src/MockUSDC.sol";

/**
 * @notice Foundry deployment script for Arc Testnet.
 *
 * Usage (Arc Testnet with real USDC):
 *   forge script script/Deploy.s.sol \
 *     --rpc-url https://rpc.testnet.arc.network \
 *     --private-key $ARC_OPERATOR_PRIVATE_KEY \
 *     --broadcast \
 *     --sig "run(address,address,address)" \
 *     <USDC_ADDRESS> <RESOLVER_ADDRESS> <ADMIN_ADDRESS>
 *
 * Usage (local Anvil with MockUSDC):
 *   forge script script/Deploy.s.sol \
 *     --rpc-url http://localhost:8545 \
 *     --private-key $ANVIL_PRIVATE_KEY \
 *     --broadcast \
 *     --sig "runLocal()"
 */

import "forge-std/Script.sol";

contract DeployScript is Script {

    // ── Arc Testnet USDC (Circle-issued) ──────────────────────────────────────
    address constant ARC_TESTNET_USDC = 0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238;

    // ── Deploy on Arc Testnet with real USDC ─────────────────────────────────
    function run(
        address usdcAddress,
        address resolverAddress,
        address adminAddress
    ) external {
        vm.startBroadcast();

        ArcaneSettlement settlement = new ArcaneSettlement(
            usdcAddress,
            resolverAddress,
            adminAddress
        );

        console.log("ArcaneSettlement deployed at:", address(settlement));
        console.log("USDC address:               ", usdcAddress);
        console.log("Resolver:                   ", resolverAddress);
        console.log("Admin:                      ", adminAddress);

        vm.stopBroadcast();
    }

    // ── Deploy locally with MockUSDC for testing ──────────────────────────────
    function runLocal() external {
        vm.startBroadcast();

        MockUSDC mockUsdc = new MockUSDC();
        console.log("MockUSDC deployed at:", address(mockUsdc));

        address deployer = msg.sender;
        ArcaneSettlement settlement = new ArcaneSettlement(
            address(mockUsdc),
            deployer, // resolver = deployer for local testing
            deployer  // admin   = deployer for local testing
        );

        console.log("ArcaneSettlement deployed at:", address(settlement));

        // Mint some test USDC to the deployer
        mockUsdc.faucet(deployer, 100_000 * 1e6); // 100,000 USDC
        console.log("Minted 100,000 MockUSDC to deployer");

        vm.stopBroadcast();
    }
}
