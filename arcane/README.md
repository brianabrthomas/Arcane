# Arcane — Legal Alpha Exchange

**An agentic prediction-market platform that converts public-company patent litigation into tradable, USDC-settled probability markets.**

Autonomous research agents discover lawsuits, summarize dockets, extract statutory catalysts, find precedent, and forecast outcomes — paying each other in **USDC nanopayments** for every task. An on-chain-style **LMSR automated market maker** prices YES/NO contracts in real time, so a market trading at $0.62 *is* a 62% implied probability of that legal outcome. The whole thing runs behind a dark "litigation terminal" UI.

> The Bloomberg Terminal, prediction market, and autonomous research economy for public-company litigation risk.

---

## Why this works as an MVP

The vertical is **pharma / biologics patent litigation**, chosen because the outcomes anchor to *deterministic statutory clocks* — which give markets built-in, objective expiration dates:

- **Hatch-Waxman** 30-month automatic stay
- **BPCIA "patent dance"** (mathematically fixed ~250-day exchange + a hard 180-day launch-notice countdown)
- **PTAB IPR** clocks (6-month institution decision, 12-month final written decision)
- **ITC Section 337** "rocket docket" target dates (set within 45 days, concluded in ~12–16 months)

Seed markets ship for three real disputes: **Amarin v. Hikma** (Vascepa skinny-label, AMRN), **Amgen v. Samsung Bioepis** (BPCIA, AMGN), and **Masimo v. Apple** (ITC §337, AAPL).

---

## Runs with zero credentials

The app boots fully interactive with **no API keys and no wallet**. Three independent layers each flip from `sim` to `live`:

| Layer | `sim` (default) | `live` |
|---|---|---|
| **LLM / agents** | deterministic legal heuristics | each agent's `think()` is a JSON Anthropic call |
| **Payments** | real EIP-3009 typed-data signing + synthetic batch receipt | settles on Arc Testnet via web3 |
| **Case data** | curated seed dockets | live CourtListener v4 ingestion |

In `sim` payment mode the nanopayments are **really signed** — every agent gets an ephemeral `eth_account` wallet and produces a valid EIP-712 / EIP-3009 `TransferWithAuthorization` signature. Only the final on-chain batch settlement is synthetic. Flip `PAYMENT_MODE=live` with a funded operator key and the same code path settles for real.

---

## Quick start

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate     # optional
pip install -r requirements.txt

# optional: copy and edit credentials (not required for the demo)
cp .env.example .env

uvicorn app.main:app --reload --port 8000
```

Open **http://localhost:8000** — the FastAPI app serves both the JSON API and the terminal UI. The SQLite database is regenerated and seeded on startup.

### Try the demo flow

1. Open the dashboard → browse the litigation markets.
2. Click a case → read the dossier, resolution criteria, and statutory catalysts.
3. Hit **Run agent research** → watch the 8-agent pipeline execute live, each agent paid a nanopayment that streams into the ledger; the model fair value appears next to the AMM price (the gap is the "alpha").
4. Buy YES/NO from the AMM → the price moves and volume updates.
5. The right rail shows live stats, agent reputation, and the nanopayment ledger with tx hashes.

---

## Architecture

```
frontend/index.html        single-file terminal UI (vanilla JS, no build step)
backend/app/
  main.py                  FastAPI app, routes, serves the frontend
  config.py                env-driven settings + the three modes
  models.py                companies, cases, events, catalysts, markets,
                           trades, positions, payments, resolutions, reputation
  amm/lmsr.py              Hanson LMSR market maker (cost fn, prices, quotes)
  ingest/
    courtlistener.py       CourtListener v4 REST client (best-effort)
    seed.py                curated cases + admin-approved markets
  payments/
    arc.py                 Arc Testnet web3 client (USDC balances, transfers)
    x402.py                EIP-3009 TransferWithAuthorization signer (x402 std)
    rail.py                per-agent wallet registry + settlement flow
  agents/
    roster.py              the 8 agents
    orchestrator.py        runs the pipeline, pays each agent, records outputs
  services/trading.py      trade execution + market resolution + payouts
```

### The agent economy

Eight specialized agents, each paid per task via the x402 nanopayment rail:

| Agent | Paid for |
|---|---|
| Case Scout | new case discovery |
| Docket | latest-filing summary |
| Legal Catalyst | statutory catalyst extraction |
| Precedent | comparable-case base rate |
| Probability | outcome forecast |
| Market Maker | YES/NO liquidity quote |
| Resolution | settlement verification |
| Auditor | reputation / evidence scoring |

### How pricing works

The **LMSR AMM** holds the live market price (what traders move). The **Probability Agent** produces a separate *model fair value* from the precedent base rate plus catalyst evidence. Both are shown side by side — their divergence is the tradable signal. The opening price and the agent's prior are seeded from the *same* base rate (via a shared logit), so they're consistent by construction.

---

## Circle / Arc integration

- **Arc Testnet** — Circle's stablecoin L1 (chain ID `5042002`, ~2s blocks). USDC is the **native gas token** at system contract `0x3600…0000`. We account exclusively in the **6-decimal ERC-20 interface** (the native 18-decimal interface is deliberately avoided — mixing the two is the classic Arc footgun).
- **x402 nanopayments** — sellers return HTTP `402 Payment Required` + a JSON challenge; the buyer signs an **EIP-3009 `TransferWithAuthorization`** message off-chain (zero gas) and retries with a `PAYMENT-SIGNATURE` header; Circle **Gateway** batches many authorizations into a single on-chain settlement, enabling sub-cent ($0.000001+) payments. Circle's official SDK is Node/TS; this project ships a faithful **Python** EIP-3009 signer so the whole stack stays in one language.
- **Circle Wallets** — the per-agent ephemeral keypair registry is the local analogue of Agent Wallets; swap in developer- or user-controlled wallets for production.

To go live: fund an operator wallet with testnet USDC from **https://faucet.circle.com** (select *Arc Testnet*), set `PAYMENT_MODE=live` and `OPERATOR_PRIVATE_KEY`, and the existing settlement path in `payments/rail.py` transacts on Arc.

---

## Bounty mapping

**Prediction Markets on Arc** — public corporate legal outcomes are real-world, hedgeable events; each statutory catalyst becomes a USDC-denominated binary market priced by an LMSR AMM with objective, source-backed resolution criteria and on-chain settlement receipts.

**Agentic economy with nanopayments** — the eight agents are economic actors, not decoration. They pay each other in USDC via x402/EIP-3009 for docket summaries, legal-risk scores, precedent packets, forecasts, liquidity quotes, and resolution checks — a genuine machine-to-machine marketplace.

**Ledger human-in-the-loop (design note)** — the architecture reserves high-risk actions (large trades, spending-limit changes, market resolution) for human approval while letting low-risk research nanopayments run autonomously; the `resolve` endpoint and admin-approval flag on markets are the hooks for that gate.

---

## API reference

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | app + mode + chain status |
| GET | `/api/chain` | Arc connectivity / USDC config |
| GET | `/api/markets` | all markets |
| GET | `/api/markets/{id}` | full market detail (dossier, criteria, catalysts) |
| POST | `/api/markets/{id}/research` | run the 8-agent pipeline (pays nanopayments) |
| POST | `/api/markets/{id}/trade` | buy YES/NO from the AMM |
| POST | `/api/markets/{id}/resolve` | settle a market and pay out positions |
| GET | `/api/agents` | agent roster + reputation |
| GET | `/api/payments?limit=` | nanopayment ledger |
| GET | `/api/stats` | platform totals |

---

## Notes

- Built for a hackathon as a vertical MVP, not a complete exchange.
- In a sandboxed/offline environment `chain.connected` will be `false` and CourtListener unreachable — the sim/seed fallbacks are working as designed, not a bug.
- The custom agent orchestrator is intentionally lightweight and transparent for the demo; each agent's `think()` is a standard JSON LLM call that drops cleanly into LangChain/CrewAI if desired.
