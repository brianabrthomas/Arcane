# Arcane — Prize Targeting Strategy & ArcScan Transaction Guide

## Prize Eligibility Analysis

The table below maps each available prize to Arcane's current MVP functionality, scoring each integration against the stated judging criteria.

| Prize | Value | Coverage | Gap |
|---|---|---|---|
| **Best Agentic Economy with Circle Agent Stack** | $3,500 | **Strong** | Needs real Arc tx hash |
| **Best Prediction Markets Built on Arc** | $1,500 | **Strong** | Needs real Arc tx hash |
| **Arc Continuity Track** | $2,000 | **Strong** | Needs live ArcScan proof |
| **Best Smart Contracts on Arc** | $3,500 | **Partial** | Needs deployed Solidity contract |
| **Best Chain Abstracted USDC Apps** | $3,500 | **Weak** | Needs CCTP crosschain flow |
| **Best Private Nanopayments** | $1,000 | **Partial** | Needs Dynamic + Unlink SDKs |

---

## Tier 1 — Target Immediately (Highest ROI)

### 1. Best Agentic Economy with Circle Agent Stack — $3,500

This is the **primary target**. The prize description reads almost word-for-word as a description of what Arcane already does:

> "AI agents paying for API calls, LLM inference, or data access per-use" — Arcane's 9-agent pipeline pays each agent in USDC nanopayments per research run.
> "Multi-agent systems with payment-based coordination mechanisms" — The orchestrator routes payments from Platform → Auditor → Trader → MarketMaker → Probability → Damages → Precedent → Catalyst → Docket → CaseScout.
> "Agent marketplaces with gas-free microtransactions" — The `/agents` endpoint exposes a live agent marketplace with per-call pricing.

**What you have:** Working agent pipeline, x402-style nanopayments, Circle Programmable Wallets integration, Arc Testnet receipts, live agent debug feed in the UI.

**What you need to add for full marks:** A real `ARC_OPERATOR_PRIVATE_KEY` so the Arc receipts are real on-chain transactions (see Section 3 below), and a 2-minute demo video.

---

### 2. Best Prediction Markets Built on Arc — $1,500

This is the **secondary target**. The prize explicitly calls for:

> "Arc's stablecoin-native infrastructure (USDC/EURC gas, deterministic finality, compliance-ready architecture)"

**What you have:** Full LMSR AMM prediction market, real CourtListener/SEC case data, USDC-denominated positions, Arc Testnet settlement receipts on every trade, Ledger human-in-the-loop policy, Aqua yield simulation.

**What you need to add:** Real Arc tx hashes visible on ArcScan (see Section 3 below).

---

### 3. Arc Continuity Track — $2,000

This track is explicitly for "founders or interesting MVPs from previous hackathons." Arcane is a complete MVP with a working frontend, backend, and blockchain integration. The requirement is simply to "add a working Arc integration" to an existing project.

**What you have:** Arc Testnet is already the core settlement layer. Every trade broadcasts a receipt to Arc.

**What you need:** A GitHub repo and a short video. This is the lowest-friction $2,000 available.

---

## Tier 2 — Achievable with 1-2 Days of Additional Work

### 4. Best Smart Contracts on Arc — $3,500

The gap here is deploying a Solidity smart contract to Arc Testnet. Currently, Arcane uses a "data-in-calldata" settlement proof (a self-send transaction with trade data encoded in the `data` field). To qualify, you need a deployed contract.

**Recommended addition:** Deploy a minimal `ArcaneMarket.sol` contract that:
- Accepts `buy(marketId, side, amount)` calls
- Emits `TradeSettled(marketId, trader, side, amount, timestamp)` events
- Stores a mapping of market IDs to cumulative YES/NO volumes

This would take approximately 4-6 hours and would make every trade a real contract call visible on ArcScan with event logs.

---

### 5. Best Private Nanopayments — $1,000

**Requirements:** Must use Dynamic (embedded wallets), Arc (testnet nanopayments), and Unlink (private transfers) — all three.

**What you have:** Arc nanopayments are already implemented. The frontend has wallet connection logic.

**Gap:** Need to replace the current MetaMask/demo wallet flow with Dynamic's embedded wallet SDK, and wrap at least one payment through Unlink's SDK for privacy. Estimated 6-8 hours.

---

## Tier 3 — Out of Scope for Current MVP

### 6. Best Chain Abstracted USDC Apps — $3,500

This requires CCTP (Cross-Chain Transfer Protocol) to move USDC across chains. Arcane is currently single-chain (Arc Testnet). Building a genuine crosschain flow would require significant additional work and is not the core value proposition of the platform.

---

## Section 3: How to Make Transactions Appear on ArcScan

Currently, the application runs in **simulation mode** — Arc receipts are generated with random mock transaction hashes that do not exist on-chain. Here is the exact step-by-step process to make real transactions appear on ArcScan.

### Step 1: Generate a Wallet

Install Foundry (the fastest method) or use any EVM wallet generator:

```bash
curl -L https://foundry.paradigm.xyz | bash
foundryup
cast wallet new
```

This outputs:
```
Address:     0xYOUR_ADDRESS
Private key: 0xYOUR_PRIVATE_KEY
```

Save both values. **Never commit the private key to git.**

### Step 2: Get Testnet USDC from Circle's Faucet

1. Go to **[faucet.circle.com](https://faucet.circle.com)**
2. Select **"Arc Testnet"** from the Network dropdown
3. Click the **USDC** card
4. Paste your wallet address
5. Click **"Send 10 USDC"**

The faucet sends 10 USDC to your wallet. You can verify receipt at:
`https://testnet.arcscan.app/address/YOUR_ADDRESS`

### Step 3: Add MetaMask Network (to see your balance in-browser)

In MetaMask → Add Network → Add manually:

| Field | Value |
|---|---|
| Network Name | Arc Testnet |
| New RPC URL | `https://rpc.testnet.arc.network` |
| Chain ID | `5042002` |
| Currency Symbol | `USDC` |
| Block Explorer | `https://testnet.arcscan.app` |

### Step 4: Configure the Arcane Backend

Edit `/home/ubuntu/arcane/.env`:

```env
ARC_OPERATOR_PRIVATE_KEY=0xYOUR_PRIVATE_KEY_FROM_STEP_1
PAYMENTS_LIVE=true
```

**Important:** The operator wallet needs at least ~0.1 USDC for gas fees. Each settlement transaction costs approximately $0.009 USDC in gas.

### Step 5: Restart the Server

```bash
cd /home/ubuntu/arcane
kill $(pgrep -f "python3 run.py")
OPENAI_API_KEY="$OPENAI_API_KEY" OPENAI_API_BASE="$OPENAI_API_BASE" python3 run.py &
```

### Step 6: Verify on ArcScan

After making a trade in the UI, the `arc_receipt` in the API response will contain a real `tx_hash` and `explorer_url`. Open that URL directly:

```
https://testnet.arcscan.app/tx/0xYOUR_REAL_TX_HASH
```

You will see:
- Transaction hash
- Block number and timestamp
- From/To address (your operator wallet)
- Gas used (in USDC)
- Input data: `ARCANE:TRADE:<market_id>:<side>:<amount>USDC`

### What the ArcScan Transaction Proves

Each on-chain transaction serves as an **immutable settlement receipt** proving:
1. The trade occurred at a specific block height
2. The market ID, side (YES/NO), and USDC amount
3. The operator wallet authorized the settlement

This is the core value proposition for the **Best Prediction Markets on Arc** prize — deterministic, auditable, stablecoin-native settlement.

---

## Required Materials for All Prize Submissions

All prizes share the same submission requirements:

1. **Functional MVP** — Live at the public URL (already running)
2. **Architecture diagram** — Available in `architecture_design.md`
3. **Video demonstration** — Record a 2-3 minute screen capture showing: wallet connection → market selection → agent research pipeline → trade execution → ArcScan receipt
4. **GitHub/Replit repo** — Push the `/home/ubuntu/arcane` directory to a public GitHub repo
5. **README** — Describe each blockchain integration (Arc, Circle, Ledger) and what is specifically live vs. simulated

---

## Additional API Keys Needed

| Key | Purpose | Where to Get | Required For |
|---|---|---|---|
| `ARC_OPERATOR_PRIVATE_KEY` | Real on-chain Arc Testnet transactions | `cast wallet new` + `faucet.circle.com` | All Tier 1 prizes |
| `COURTLISTENER_TOKEN` | Live docket data (vs. seeded data) | [courtlistener.com/sign-in](https://www.courtlistener.com/sign-in/) | Stronger demo narrative |
| `CIRCLE_API_KEY` | Real Circle Programmable Wallet transfers | [developers.circle.com](https://developers.circle.com) | Best Agentic Economy (full marks) |
