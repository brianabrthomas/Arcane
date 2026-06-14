"""
e2e_test.py — Full end-to-end integration test for Arcane MVP.

Tests the complete settlement lifecycle:
  1. Health check
  2. List markets
  3. Get quote
  4. Execute trade (LMSR AMM + Arc receipt)
  5. Deploy market on-chain (createMarket)
  6. Verify contract status
  7. Get EIP-712 signing artifact
  8. Propose resolution (proposeResolution)
  9. Finalize resolution (finalizeResolution)
  10. Claim payout (claimPayout)
  11. x402 payment requirements
  12. x402 batch settlement
  13. Agent research pipeline
  14. Ledger policy check
"""
import json
import sys
import time
import requests

BASE = "http://localhost:8000"
DEMO_WALLET = "0xDemoTrader0000000000000000000000000001"
PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
INFO = "\033[94mℹ\033[0m"

results = []

def check(name, ok, detail=""):
    status = PASS if ok else FAIL
    print(f"  {status} {name}" + (f" — {detail}" if detail else ""))
    results.append((name, ok, detail))
    return ok

def api(method, path, **kwargs):
    url = f"{BASE}{path}"
    r = requests.request(method, url, timeout=30, **kwargs)
    r.raise_for_status()
    return r.json()

def run():
    print("\n" + "="*70)
    print("  ARCANE END-TO-END INTEGRATION TEST")
    print("  Arc Testnet · Circle USDC · Ledger · x402 · ArcaneSettlement.sol")
    print("="*70)

    # ── 1. Health ──────────────────────────────────────────────────────────
    print("\n[1] Health & Chain Status")
    h = api("GET", "/api/health")
    check("Server is up", h.get("app") is not None, h.get("app",""))
    chain = h.get("chain", {})
    check("Arc Testnet connected", chain.get("connected") is True,
          f"block {chain.get('latest_block')}")
    check("Chain ID is 5042002", chain.get("chain_id") == 5042002)
    check("LLM mode", h.get("llm_mode") in ("live","sim"), h.get("llm_mode"))

    # ── 2. Markets ─────────────────────────────────────────────────────────
    print("\n[2] Markets")
    markets = api("GET", "/api/markets")
    check("Markets loaded", len(markets) > 0, f"{len(markets)} markets")
    m = markets[0]
    mid = m["id"]
    check("Market has LMSR prices", 0 < m.get("price_yes", 0) < 1,
          f"YES={m.get('price_yes',0):.3f}")
    check("Market has case data", m.get("case") is not None,
          m.get("case", {}).get("caption", "")[:40])
    check("Market has on_chain_status field", "on_chain_status" in m)

    # ── 3. Stats ───────────────────────────────────────────────────────────
    print("\n[3] Platform Stats")
    stats = api("GET", "/api/stats")
    check("Stats endpoint", stats.get("open_markets") is not None,
          f"open={stats.get('open_markets')}")

    # ── 4. Quote ───────────────────────────────────────────────────────────
    print("\n[4] LMSR AMM Quote")
    q = api("POST", f"/api/markets/{mid}/quote", json={"side": "YES", "budget_usdc": 100.0})
    check("Quote returned", q.get("estimated_shares") is not None,
          f"${q.get('budget_usdc',0):.2f} for {q.get('estimated_shares',0):.2f} shares @ {q.get('avg_price',0):.3f}")
    check("Price impact calculated", q.get("price_impact") is not None,
          f"{q.get('price_impact',0):.4f}")

    # ── 5. Execute Trade ───────────────────────────────────────────────────
    print("\n[5] Execute Trade (LMSR + Arc receipt)")
    trade = api("POST", f"/api/markets/{mid}/trade", json={
        "trader": DEMO_WALLET,
        "side": "YES",
        "budget_usdc": 50.0,
    })
    check("Trade executed", trade.get("shares") is not None,
          f"shares={trade.get('shares',0):.2f}")
    arc_r = trade.get("arc_receipt", {})
    check("Arc receipt returned", arc_r.get("tx_hash") is not None,
          arc_r.get("tx_hash","")[:20])
    check("ArcScan URL present", "arcscan" in arc_r.get("explorer_url","").lower(),
          arc_r.get("explorer_url","")[:50])

    # ── 6. Deploy Market On-Chain ──────────────────────────────────────────
    print("\n[6] Deploy Market On-Chain (createMarket)")
    deploy = api("POST", f"/api/markets/{mid}/contract/create")
    check("createMarket call succeeded",
          deploy.get("status") in ("success", "simulated"),
          f"contract_id=#{deploy.get('contract_market_id')}")
    check("Contract market ID assigned",
          deploy.get("contract_market_id") is not None)
    arcscan_url = deploy.get("arcscan_url", "")
    check("ArcScan create tx URL",
          "arcscan" in arcscan_url.lower() or "0x" in arcscan_url.lower(),
          arcscan_url[:60])

    # ── 7. Contract Status ─────────────────────────────────────────────────
    print("\n[7] Contract Status")
    cs = api("GET", "/api/contract/status")
    check("Contract address present",
          cs.get("contract_address") is not None,
          cs.get("contract_address","")[:20])
    check("Chain ID matches Arc Testnet",
          cs.get("chain_id") == 5042002)
    check("ArcScan contract URL",
          "arcscan" in (cs.get("arcscan_contract") or "").lower(),
          (cs.get("arcscan_contract") or "")[:50])

    # ── 8. EIP-712 Signing Artifact ────────────────────────────────────────
    print("\n[8] EIP-712 Signing Artifacts (Ledger clear-signing)")
    art = api("POST", f"/api/markets/{mid}/signing-artifacts/trade", json={
        "wallet": DEMO_WALLET,
        "side": "YES",
        "amount_usdc": 50.0,
        "max_price": 0.99,
    })
    check("Trade signing artifact returned", art.get("typed_data") is not None)
    check("EIP-712 domain present",
          art.get("typed_data", {}).get("domain") is not None)
    check("Ledger display fields present",
          art.get("ledger_display") is not None,
          str(art.get("ledger_display",{})))
    check("Ledger required flag correct (< $100 = False)",
          art.get("requires_ledger") is False)

    # Test resolution artifact (always requires Ledger)
    res_art = api("POST",
        f"/api/markets/{mid}/signing-artifacts/resolution?outcome=YES&resolver={DEMO_WALLET}")
    check("Resolution signing artifact returned",
          res_art.get("typed_data") is not None)
    check("Resolution always requires Ledger",
          res_art.get("requires_ledger") is True)

    # ── 9. Propose Resolution ──────────────────────────────────────────────
    print("\n[9] Propose Resolution (proposeResolution)")
    prop = api("POST", f"/api/markets/{mid}/contract/propose-resolution", json={
        "outcome": "YES",
        "evidence_uri": f"https://www.courtlistener.com/docket/{mid[:8]}/",
        "rationale": "Court ruled in favor of plaintiff — patent infringement confirmed",
        "resolver": DEMO_WALLET,
        "ledger_sig": "0xledger_demo_sig",
    })
    check("Resolution proposed",
          prop.get("status") in ("success", "simulated"),
          f"outcome={prop.get('outcome')}")
    check("Dispute window set",
          prop.get("dispute_ends_at") is not None,
          prop.get("dispute_ends_at","")[:20])
    check("ArcScan resolution tx",
          prop.get("arcscan_url") is not None,
          (prop.get("arcscan_url") or "")[:50])

    # ── 10. Finalize Resolution ────────────────────────────────────────────
    print("\n[10] Finalize Resolution (finalizeResolution)")
    fin = api("POST", f"/api/markets/{mid}/contract/finalize")
    check("Finalization call succeeded",
          fin.get("status") in ("success", "simulated"),
          fin.get("message",""))
    check("ArcScan finalize tx",
          fin.get("arcscan_url") is not None,
          (fin.get("arcscan_url") or "")[:50])

    # ── 11. Claim Payout ───────────────────────────────────────────────────
    print("\n[11] Claim Payout (claimPayout)")
    claim = api("POST", f"/api/markets/{mid}/contract/claim-payout", json={
        "wallet": DEMO_WALLET,
    })
    check("Payout claim processed",
          claim.get("status") in ("success", "simulated", "no_position"),
          claim.get("message",""))

    # ── 12. x402 Payment Requirements ─────────────────────────────────────
    print("\n[12] x402 Circle Gateway Nanopayments")
    reqs = api("GET", "/api/x402/requirements")
    check("x402 requirements endpoint", reqs.get("endpoints") is not None,
          f"{len(reqs.get('endpoints',[]))} endpoints priced")
    check("USDC address present",
          reqs.get("usdc_address") is not None,
          reqs.get("usdc_address","")[:20])
    check("Chain ID in x402 config",
          reqs.get("chain_id") == 5042002)

    # x402 payments list
    pmts = api("GET", "/api/x402/payments?limit=10")
    check("x402 payments list", isinstance(pmts, list))

    # ── 13. x402 Batch Settlement ──────────────────────────────────────────
    print("\n[13] x402 Batch Settlement")
    batch = api("POST", "/api/x402/settle-batch")
    check("Batch settlement processed",
          batch.get("batch_id") is not None,
          f"settled={batch.get('settled_count',0)} payments")

    # ── 14. Agent Research ─────────────────────────────────────────────────
    print("\n[14] Agent Research Pipeline")
    try:
        research = api("POST", f"/api/markets/{mid}/research")
        check("Agent pipeline ran",
              research.get("agent_count", 0) > 0,
              f"{research.get('agent_count')} agents")
        check("Probability forecast returned",
              research.get("probability_yes") is not None,
              f"P(YES)={research.get('probability_yes',0):.3f}")
        check("x402 payments generated",
              research.get("total_paid_usdc", 0) > 0,
              f"${research.get('total_paid_usdc',0):.4f} USDC")
    except Exception as e:
        check("Agent pipeline", False, str(e)[:60])

    # ── 15. Ledger Policy ──────────────────────────────────────────────────
    print("\n[15] Ledger Policy")
    lp = api("GET", "/api/ledger/policy")
    check("Ledger policy endpoint", lp.get("threshold_usdc") is not None,
          f"threshold=${lp.get('threshold_usdc')}")
    check("Ledger DMK snippet present",
          lp.get("dmk_snippet") is not None)

    # ── 16. Wallet Positions ───────────────────────────────────────────────
    print("\n[16] Wallet Positions")
    bal = api("GET", f"/api/wallet/{DEMO_WALLET}/balance")
    check("Wallet balance endpoint", bal.get("available_usdc") is not None,
          f"${bal.get('available_usdc',0):.2f} USDC")
    positions = api("GET", f"/api/wallet/{DEMO_WALLET}/positions")
    check("Positions endpoint", isinstance(positions, list),
          f"{len(positions)} positions")

    # ── Summary ────────────────────────────────────────────────────────────
    print("\n" + "="*70)
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    pct = 100 * passed // total
    print(f"  RESULT: {passed}/{total} checks passed ({pct}%)")
    if passed == total:
        print(f"  {PASS} ALL CHECKS PASSED — Arcane is bounty-ready!")
    else:
        failed = [(n, d) for n, ok, d in results if not ok]
        print(f"  {FAIL} {len(failed)} checks failed:")
        for n, d in failed:
            print(f"     - {n}: {d}")
    print("="*70 + "\n")

    return passed, total

if __name__ == "__main__":
    passed, total = run()
    sys.exit(0 if passed == total else 1)
