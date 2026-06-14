# Arcane — Discretionary Decision Log

As requested, here is the log of discretionary technical decisions made during the implementation of the Arcane Legal Alpha Exchange MVP:

## 1. Simulation Modes & Fallbacks
- **Decision**: Implemented seamless simulation fallbacks for all external integrations (Arc, Circle, Ledger, CourtListener, OpenAI).
- **Rationale**: Ensures the MVP can run immediately out-of-the-box without requiring the user to supply 5 different API keys and private keys upfront. The application gracefully degrades to mock data, simulated blockchain receipts, and deterministic LLM responses while maintaining the exact same data shapes and DB mutations as the live paths.

## 2. LMSR AMM Liquidity Parameter ($b$)
- **Decision**: Set the default LMSR liquidity parameter $b=100.0$ for seeded markets.
- **Rationale**: $b=100$ provides a reasonable price impact curve for small MVP test trades ($10-$100 USDC). A larger $b$ would make prices too sticky for a demo, while a smaller $b$ would cause extreme slippage on standard $25 trades.

## 3. x402 Agent Payment Flow
- **Decision**: Implemented agent-to-agent nanopayments using an internal ledger backed by Circle Programmable Wallets, rather than raw on-chain transfers for every LLM call.
- **Rationale**: Agents run in a pipeline (9 agents per research run). Executing 9 separate on-chain EVM transactions per run would incur unacceptable gas costs and latency. Circle Programmable Wallets (or an internal ledger) allows instant, feeless micro-settlement, which is critical for an AI agent economy.

## 4. Ledger Policy Thresholds
- **Decision**: Hardcoded the default Ledger hardware approval threshold to $100.00 USDC for trades, and required it universally for market resolution.
- **Rationale**: $100 is a sensible boundary between "casual agentic spending" and "high-risk capital deployment". Market resolution is a critical state change that affects all participants, so it strictly requires a human-in-the-loop signature.

## 5. Aqua Yield Simulation
- **Decision**: Simulated Aqua (1inch) yield at a flat 5.2% APY rather than integrating live DeFi yield routing.
- **Rationale**: True DeFi yield routing requires complex smart contract integrations and live liquidity pool monitoring. For the MVP, simulating the yield offset demonstrates the UX value proposition (earning yield on locked prediction market collateral) without the overhead of deploying full DeFi contracts.

## 6. Frontend Architecture
- **Decision**: Built the frontend as a single-page vanilla HTML/JS/CSS application (`index.html`) rather than a heavy React/Next.js stack.
- **Rationale**: Maximizes portability and minimizes build steps for the MVP. It directly consumes the FastAPI backend and includes all necessary UI components (wallet connection, market lists, agent feed, trading modals) in one cohesive file.

## 7. Database Choice
- **Decision**: Used SQLite with SQLAlchemy for the MVP.
- **Rationale**: Zero-config setup allows the application to be tested immediately. SQLAlchemy ORM ensures that migrating to PostgreSQL for production is a trivial configuration change (`DATABASE_URL`).
