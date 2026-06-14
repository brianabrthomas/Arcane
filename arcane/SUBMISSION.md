# Arcane — ETH Global NYC Submission

## Project Name
**Arcane · Agentic Legal Prediction Markets**

## Tagline
Autonomous AI agents pricing legal risk via LMSR AMM, settled on Arc Testnet with Circle USDC nanopayments and Ledger hardware policy.

## Problem
High-stakes patent litigation and regulatory actions move billions of dollars in equity markets, yet retail investors and smaller firms lack the resources to parse 500-page docket filings in real-time. Prediction markets (like Polymarket or Kalshi) are excellent price discovery mechanisms, but they rely on human liquidity providers who cannot read fast enough to price intra-day legal risk accurately.

## Solution
Arcane is an autonomous prediction market where the liquidity providers and researchers are AI agents. We built an ensemble of 9 specialized agents (CaseScout, DocketAgent, PrecedentAgent, ProbabilityAgent, etc.) that monitor the CourtListener API, parse SEC filings, and trade against an LMSR AMM. 

To make this economically viable, we integrated the full **Circle/Arc/Ledger** stack:
1. **Agent Economy:** Agents pay each other micro-fees for compute using Circle Gateway x402 nanopayments.
2. **Deterministic Settlement:** All trades and resolutions are settled on `ArcaneSettlement.sol` deployed to the Arc Testnet.
3. **Hardware Security:** We implemented a dynamic risk engine. Routine agent trades are signed via software, but human trades over $100 and all market resolutions strictly require Ledger hardware device approval using the Ledger DMK for EIP-712 clear-signing.

---

## Prize Targeting Narrative

### 🥇 Primary Target: Best Agentic Economy with Circle Agent Stack ($3,500)
Arcane is a literal implementation of the Circle Agent Stack vision. We built a 9-agent pipeline where compute is monetized via x402 headers. When a user clicks "Run Agent Analysis", the frontend authorizes a USDC payment to the Orchestrator agent via EIP-3009. The Orchestrator then delegates tasks—paying `CaseScout` $0.005 to fetch PDFs, and `ProbabilityAgent` $0.01 to run the forecast model. The backend acts as the Circle Gateway, batch-settling these nanopayments and logging them in real-time in the UI.

### 🥈 Secondary Target: Best Prediction Markets Built on Arc ($1,500)
Arcane is a fully functional prediction market. We wrote a custom conditional settlement contract (`ArcaneSettlement.sol`) and deployed it to Arc Testnet. Every market has a 7-state lifecycle. When a user trades, the backend calculates the LMSR price impact, generates an EIP-712 payload, and relays the `buy()` call to the contract, escrowing USDC. We built a full 24-hour on-chain dispute window for resolutions, and users can directly claim payouts from the contract. Every action generates an ArcScan receipt visible in the UI.

### 🥉 Tertiary Target: Ledger — Best integration of Clear Signing ($2,000)
We integrated the Ledger DMK directly into our risk policy engine. We don't just prompt for signatures blindly; we dynamically route based on risk. Trades under $100 use standard web3 signatures. Trades over $100 trigger the Ledger DMK flow. We built custom EIP-712 typed data builders in Python so the Ledger device clearly displays the exact `Market`, `Side`, `Amount`, and `Max Price` to the user before they sign, preventing blind-signing attacks on high-value trades.

### 🏅 Fallback Target: Arc Continuity Track ($2,000)
Arcane is a complete, working MVP with a beautiful Kalshi-style UI, a robust FastAPI backend, and a verified Arc Testnet integration. We have provided a full video demo, a public GitHub repo, and architecture diagrams, easily qualifying for the continuity track.

---

## Links
- **Live Demo:** [Arcane Live MVP](https://8000-iwt1ip4bihv7j9swzkvnu-ed39edef.us2.manus.computer/)
- **Architecture Diagram:** See `architecture.png` in repo.
- **Demo Script:** See `DEMO_SCRIPT.md` for exact steps to verify integrations.
