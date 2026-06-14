# Phase 1: Confirmation of Details, Discretionary Decisions, and API Keys

Based on the documentation provided in `pasted_content_9.txt`, the Circle Gateway Nanopayments docs, and the Ledger ETH Global NYC docs, here is the confirmed integration plan to target the **Best Smart Contracts on Arc with Advanced Stablecoin Logic** bounty, while keeping the **Agentic Economy** and **Ledger** bounties intact.

## 1. Discretionary Decisions for the Smart Contract

- **Contract Architecture**: The LMSR AMM will remain off-chain (in the FastAPI backend) for pricing and quote generation. The `ArcaneSettlement.sol` contract will handle **escrow, conditional settlement, and payouts**. This satisfies the "multi-step settlement" and "conditional flows" requirements without the gas overhead of running complex LMSR math on-chain.
- **USDC Handling**: The contract will strictly use the Arc Testnet USDC contract (`0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238`). Users will need to `approve` the settlement contract before trading.
- **Dispute Window**: When a market is resolved, it enters a `ResolutionProposed` state. A 24-hour dispute window begins. If undisputed, it moves to `Finalized` and payouts unlock. If disputed, an admin (Ledger-secured) must `finalizeResolution`.
- **Void Logic**: If a legal case is sealed or unresolvable, the market can be voided. Users can then call `claimRefund` to get their proportional collateral back.

## 2. Discretionary Decisions for Nanopayments (x402)

- **x402 Implementation**: The Circle Gateway Nanopayments docs specify an EIP-3009 off-chain authorization flow. Since we are building an MVP and do not have a registered Circle Developer Entity Secret configured in the sandbox, we will **simulate the x402 EIP-3009 signature flow** in the backend. The backend will generate a simulated EIP-3009 signature, verify it, and log the "batched settlement" event. This perfectly demonstrates the *architecture* of gas-free agent-to-agent nanopayments without requiring the complex Circle API onboarding.

## 3. Discretionary Decisions for Ledger

- **Clear Signing**: Ledger will be required for three high-risk actions:
  1. Large trades (>= 100 USDC)
  2. Agent spending policy updates
  3. Market resolution (admin action)
- The backend already has `ledger.py` with EIP-712 schemas. We will integrate these schemas into the frontend UI to simulate the clear-signing experience.

## 4. Required API Keys and Environment

- `ARC_OPERATOR_PRIVATE_KEY`: Required to deploy the `ArcaneSettlement.sol` contract and execute admin functions (resolving, voiding). We will generate a fresh Foundry wallet and fund it from the Arc faucet.
- `OPENAI_API_KEY`: Provided by sandbox.
- `COURTLISTENER_TOKEN`: Optional (seeded data used if absent).

## 5. Chronological Build Sequence

1. **Phase 2**: Write `ArcaneSettlement.sol`.
2. **Phase 3**: Generate Foundry wallet, get testnet USDC, compile, and deploy the contract to Arc Testnet.
3. **Phase 4**: Update DB models to track on-chain market IDs and settlement states.
4. **Phase 5**: Build `contract.py` in the backend to interact with the deployed contract using `web3.py`.
5. **Phase 6**: Upgrade the agent nanopayment flow to explicitly model the x402 EIP-3009 architecture.
6. **Phase 7**: Update FastAPI routes to use the contract for trades and resolutions.
7. **Phase 8**: Upgrade the frontend to show the "Settlement Contract" panel, ArcScan links, and Ledger modals.
8. **Phase 9**: End-to-end testing.
9. **Phase 10**: Final delivery.
