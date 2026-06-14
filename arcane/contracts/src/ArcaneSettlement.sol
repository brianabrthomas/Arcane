// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title ArcaneSettlement
 * @notice USDC conditional settlement contract for legal prediction markets on Arc Testnet.
 *
 * Settlement lifecycle per market:
 *   Open → Closed → ResolutionProposed → (Disputed →) Finalized → [PayoutClaimed | Voided → Refunded]
 *
 * Key design decisions:
 * - LMSR AMM pricing is computed off-chain (FastAPI backend). The contract enforces
 *   collateral custody, share accounting, and conditional payout only.
 * - Shares are denominated in USDC with 6 decimals (Arc Testnet USDC).
 * - The resolver role is held by the Arcane backend operator wallet.
 * - The admin role is held by a Ledger-secured wallet for dispute resolution.
 * - A 24-hour dispute window separates ResolutionProposed from Finalized.
 * - Void/refund logic handles sealed, transferred, or unresolvable legal cases.
 */

interface IERC20 {
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function transfer(address to, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
    function allowance(address owner, address spender) external view returns (uint256);
}

contract ArcaneSettlement {

    // ─────────────────────────────────────────────────────────────────────────
    // State types
    // ─────────────────────────────────────────────────────────────────────────

    enum Status {
        Open,               // 0 — accepting trades
        Closed,             // 1 — trading halted, awaiting resolution
        ResolutionProposed, // 2 — resolver has proposed an outcome
        Disputed,           // 3 — admin has disputed the proposed outcome
        Finalized,          // 4 — outcome confirmed, payouts unlocked
        Voided              // 5 — market voided, refunds available
    }

    enum Outcome {
        None,   // 0 — unresolved
        Yes,    // 1 — YES outcome confirmed
        No,     // 2 — NO outcome confirmed
        Void    // 3 — market voided
    }

    struct Market {
        // Identity
        string  question;           // Human-readable market question
        string  resolutionSource;   // e.g. "CourtListener docket #..." or "SEC EDGAR 8-K"
        string  evidenceURI;        // URI to the public evidence document / court order
        // Timing
        uint256 closeTime;          // Unix timestamp — trading halts after this
        uint256 disputeEndsAt;      // Unix timestamp — dispute window closes after this
        uint256 disputeWindowSecs;  // Configurable dispute window (default 86400 = 24h)
        // State machine
        Status  status;
        Outcome proposedOutcome;
        Outcome finalOutcome;
        // Share accounting
        uint256 totalYesShares;     // Cumulative YES shares issued (USDC units, 6 dec)
        uint256 totalNoShares;      // Cumulative NO shares issued
        uint256 totalCollateral;    // Total USDC escrowed in this market
        // Metadata
        address creator;
        uint256 createdAt;
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Storage
    // ─────────────────────────────────────────────────────────────────────────

    IERC20  public immutable usdc;
    address public           resolver;   // Arcane backend operator — proposes resolutions
    address public           admin;      // Ledger-secured admin — disputes and finalizes

    uint256 public marketCount;
    mapping(uint256 => Market)                                   public markets;
    mapping(uint256 => mapping(address => uint256))              public yesShares;
    mapping(uint256 => mapping(address => uint256))              public noShares;
    mapping(uint256 => mapping(address => bool))                 public claimed;
    mapping(uint256 => mapping(address => uint256))              public collateralDeposited; // per-user, for refund

    uint256 public constant DEFAULT_DISPUTE_WINDOW = 86400; // 24 hours

    // ─────────────────────────────────────────────────────────────────────────
    // Events
    // ─────────────────────────────────────────────────────────────────────────

    event MarketCreated(
        uint256 indexed marketId,
        string  question,
        string  resolutionSource,
        uint256 closeTime,
        address creator
    );

    event TradeExecuted(
        uint256 indexed marketId,
        address indexed trader,
        bool    isYes,
        uint256 usdcAmount,
        uint256 sharesOut
    );

    event MarketClosed(uint256 indexed marketId);

    event ResolutionProposed(
        uint256 indexed marketId,
        Outcome outcome,
        string  evidenceURI,
        uint256 disputeEndsAt
    );

    event ResolutionDisputed(uint256 indexed marketId, address disputer);

    event ResolutionFinalized(uint256 indexed marketId, Outcome outcome);

    event PayoutClaimed(
        uint256 indexed marketId,
        address indexed user,
        uint256 amount
    );

    event MarketVoided(uint256 indexed marketId, string reasonURI);

    event RefundClaimed(
        uint256 indexed marketId,
        address indexed user,
        uint256 amount
    );

    event ResolverUpdated(address indexed newResolver);
    event AdminUpdated(address indexed newAdmin);

    // ─────────────────────────────────────────────────────────────────────────
    // Modifiers
    // ─────────────────────────────────────────────────────────────────────────

    modifier onlyResolver() {
        require(msg.sender == resolver, "ArcaneSettlement: caller is not resolver");
        _;
    }

    modifier onlyAdmin() {
        require(msg.sender == admin, "ArcaneSettlement: caller is not admin");
        _;
    }

    modifier onlyResolverOrAdmin() {
        require(
            msg.sender == resolver || msg.sender == admin,
            "ArcaneSettlement: caller is not resolver or admin"
        );
        _;
    }

    modifier marketExists(uint256 marketId) {
        require(marketId < marketCount, "ArcaneSettlement: market does not exist");
        _;
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Constructor
    // ─────────────────────────────────────────────────────────────────────────

    constructor(address _usdc, address _resolver, address _admin) {
        require(_usdc     != address(0), "ArcaneSettlement: zero usdc");
        require(_resolver != address(0), "ArcaneSettlement: zero resolver");
        require(_admin    != address(0), "ArcaneSettlement: zero admin");
        usdc     = IERC20(_usdc);
        resolver = _resolver;
        admin    = _admin;
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Market creation
    // ─────────────────────────────────────────────────────────────────────────

    /**
     * @notice Create a new legal prediction market.
     * @param question         The binary market question.
     * @param resolutionSource Public source that will determine the outcome.
     * @param closeTime        Unix timestamp after which trading halts.
     * @return marketId        The ID of the newly created market.
     */
    function createMarket(
        string calldata question,
        string calldata resolutionSource,
        uint256         closeTime
    ) external onlyResolverOrAdmin returns (uint256 marketId) {
        require(bytes(question).length > 0,         "ArcaneSettlement: empty question");
        require(closeTime > block.timestamp,         "ArcaneSettlement: closeTime in past");

        marketId = marketCount++;

        Market storage m = markets[marketId];
        m.question          = question;
        m.resolutionSource  = resolutionSource;
        m.closeTime         = closeTime;
        m.disputeWindowSecs = DEFAULT_DISPUTE_WINDOW;
        m.status            = Status.Open;
        m.proposedOutcome   = Outcome.None;
        m.finalOutcome      = Outcome.None;
        m.creator           = msg.sender;
        m.createdAt         = block.timestamp;

        emit MarketCreated(marketId, question, resolutionSource, closeTime, msg.sender);
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Trading
    // ─────────────────────────────────────────────────────────────────────────

    /**
     * @notice Buy YES or NO shares in a market.
     *         The caller must have approved this contract to spend `usdcAmount`.
     *         `sharesOut` is computed off-chain by the LMSR AMM and passed in for
     *         on-chain accounting. The contract does NOT verify LMSR math; it only
     *         enforces collateral custody and share issuance.
     *
     * @param marketId    Target market ID.
     * @param isYes       True for YES shares, false for NO shares.
     * @param usdcAmount  USDC to deposit (6 decimals).
     * @param sharesOut   Shares to credit to the caller (must be > 0).
     */
    function buy(
        uint256 marketId,
        bool    isYes,
        uint256 usdcAmount,
        uint256 sharesOut
    ) external marketExists(marketId) {
        Market storage m = markets[marketId];
        require(m.status == Status.Open,                  "ArcaneSettlement: market not open");
        require(block.timestamp < m.closeTime,            "ArcaneSettlement: market closed");
        require(usdcAmount > 0,                           "ArcaneSettlement: zero amount");
        require(sharesOut  > 0,                           "ArcaneSettlement: zero shares");

        // Pull USDC from trader into this contract
        bool ok = usdc.transferFrom(msg.sender, address(this), usdcAmount);
        require(ok, "ArcaneSettlement: USDC transfer failed");

        // Credit shares and collateral
        if (isYes) {
            yesShares[marketId][msg.sender] += sharesOut;
            m.totalYesShares                += sharesOut;
        } else {
            noShares[marketId][msg.sender]  += sharesOut;
            m.totalNoShares                 += sharesOut;
        }
        m.totalCollateral                   += usdcAmount;
        collateralDeposited[marketId][msg.sender] += usdcAmount;

        emit TradeExecuted(marketId, msg.sender, isYes, usdcAmount, sharesOut);
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Market lifecycle
    // ─────────────────────────────────────────────────────────────────────────

    /**
     * @notice Close trading on a market. Can be called by resolver/admin once
     *         closeTime has passed, or at any time by admin for emergency closure.
     */
    function closeMarket(uint256 marketId) external onlyResolverOrAdmin marketExists(marketId) {
        Market storage m = markets[marketId];
        require(m.status == Status.Open, "ArcaneSettlement: market not open");
        // Allow early close by admin; resolver must wait for closeTime
        if (msg.sender == resolver) {
            require(block.timestamp >= m.closeTime, "ArcaneSettlement: closeTime not reached");
        }
        m.status = Status.Closed;
        emit MarketClosed(marketId);
    }

    /**
     * @notice Propose a resolution outcome. Starts the dispute window.
     * @param marketId    Target market ID.
     * @param outcome     Yes, No, or Void.
     * @param evidenceURI URI to the public legal evidence document.
     */
    function proposeResolution(
        uint256        marketId,
        Outcome        outcome,
        string calldata evidenceURI
    ) external onlyResolver marketExists(marketId) {
        Market storage m = markets[marketId];
        require(
            m.status == Status.Closed,
            "ArcaneSettlement: market must be closed first"
        );
        require(
            outcome == Outcome.Yes || outcome == Outcome.No || outcome == Outcome.Void,
            "ArcaneSettlement: invalid outcome"
        );
        require(bytes(evidenceURI).length > 0, "ArcaneSettlement: empty evidenceURI");

        m.proposedOutcome = outcome;
        m.evidenceURI     = evidenceURI;
        m.disputeEndsAt   = block.timestamp + m.disputeWindowSecs;
        m.status          = Status.ResolutionProposed;

        emit ResolutionProposed(marketId, outcome, evidenceURI, m.disputeEndsAt);
    }

    /**
     * @notice Dispute a proposed resolution during the dispute window.
     *         Only admin (Ledger-secured) may dispute.
     */
    function disputeResolution(uint256 marketId) external onlyAdmin marketExists(marketId) {
        Market storage m = markets[marketId];
        require(
            m.status == Status.ResolutionProposed,
            "ArcaneSettlement: no resolution to dispute"
        );
        require(
            block.timestamp < m.disputeEndsAt,
            "ArcaneSettlement: dispute window closed"
        );
        m.status = Status.Disputed;
        emit ResolutionDisputed(marketId, msg.sender);
    }

    /**
     * @notice Finalize the resolution.
     *         - If ResolutionProposed and dispute window has passed: anyone can call.
     *         - If Disputed: only admin can call (Ledger-secured override).
     *         - Admin may also override a proposed outcome with a new one when finalizing
     *           a disputed market.
     */
    function finalizeResolution(
        uint256 marketId,
        Outcome overrideOutcome  // Pass Outcome.None to keep the proposed outcome
    ) external marketExists(marketId) {
        Market storage m = markets[marketId];

        if (m.status == Status.ResolutionProposed) {
            // Auto-finalize after dispute window — anyone can trigger
            require(
                block.timestamp >= m.disputeEndsAt,
                "ArcaneSettlement: dispute window still open"
            );
            m.finalOutcome = m.proposedOutcome;
        } else if (m.status == Status.Disputed) {
            // Admin override required for disputed markets
            require(msg.sender == admin, "ArcaneSettlement: only admin can finalize disputed");
            if (overrideOutcome != Outcome.None) {
                m.finalOutcome = overrideOutcome;
            } else {
                m.finalOutcome = m.proposedOutcome;
            }
        } else {
            revert("ArcaneSettlement: market not in finalizable state");
        }

        if (m.finalOutcome == Outcome.Void) {
            m.status = Status.Voided;
            emit MarketVoided(marketId, m.evidenceURI);
        } else {
            m.status = Status.Finalized;
        }

        emit ResolutionFinalized(marketId, m.finalOutcome);
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Payout and refund
    // ─────────────────────────────────────────────────────────────────────────

    /**
     * @notice Claim payout for winning shares after finalization.
     *         Payout is proportional: user_winning_shares / total_winning_shares * totalCollateral.
     */
    function claimPayout(uint256 marketId) external marketExists(marketId) {
        Market storage m = markets[marketId];
        require(m.status == Status.Finalized,             "ArcaneSettlement: not finalized");
        require(!claimed[marketId][msg.sender],            "ArcaneSettlement: already claimed");

        uint256 userShares;
        uint256 totalWinningShares;

        if (m.finalOutcome == Outcome.Yes) {
            userShares          = yesShares[marketId][msg.sender];
            totalWinningShares  = m.totalYesShares;
        } else if (m.finalOutcome == Outcome.No) {
            userShares          = noShares[marketId][msg.sender];
            totalWinningShares  = m.totalNoShares;
        } else {
            revert("ArcaneSettlement: use claimRefund for voided markets");
        }

        require(userShares > 0,           "ArcaneSettlement: no winning shares");
        require(totalWinningShares > 0,   "ArcaneSettlement: no winning shares in market");

        claimed[marketId][msg.sender] = true;

        // Proportional payout: userShares / totalWinningShares * totalCollateral
        uint256 payout = (userShares * m.totalCollateral) / totalWinningShares;
        require(payout > 0, "ArcaneSettlement: zero payout");

        bool ok = usdc.transfer(msg.sender, payout);
        require(ok, "ArcaneSettlement: USDC payout failed");

        emit PayoutClaimed(marketId, msg.sender, payout);
    }

    /**
     * @notice Void a market (admin only). Used when a legal case becomes unresolvable.
     *         After voiding, users can claim refunds proportional to their deposited collateral.
     */
    function voidMarket(
        uint256        marketId,
        string calldata reasonURI
    ) external onlyAdmin marketExists(marketId) {
        Market storage m = markets[marketId];
        require(
            m.status == Status.Open     ||
            m.status == Status.Closed   ||
            m.status == Status.ResolutionProposed ||
            m.status == Status.Disputed,
            "ArcaneSettlement: cannot void finalized market"
        );
        m.status       = Status.Voided;
        m.finalOutcome = Outcome.Void;
        m.evidenceURI  = reasonURI;
        emit MarketVoided(marketId, reasonURI);
        emit ResolutionFinalized(marketId, Outcome.Void);
    }

    /**
     * @notice Claim a refund from a voided market.
     *         Refund is proportional to the user's deposited collateral.
     */
    function claimRefund(uint256 marketId) external marketExists(marketId) {
        Market storage m = markets[marketId];
        require(m.status == Status.Voided, "ArcaneSettlement: market not voided");
        require(!claimed[marketId][msg.sender], "ArcaneSettlement: already claimed");

        uint256 userCollateral = collateralDeposited[marketId][msg.sender];
        require(userCollateral > 0, "ArcaneSettlement: no collateral to refund");

        claimed[marketId][msg.sender] = true;

        // Proportional refund: userCollateral / totalCollateral * contractBalance
        // Using contractBalance (not totalCollateral) to handle any rounding safely
        uint256 contractBalance = usdc.balanceOf(address(this));
        uint256 refund = (userCollateral * contractBalance) / m.totalCollateral;
        require(refund > 0, "ArcaneSettlement: zero refund");

        bool ok = usdc.transfer(msg.sender, refund);
        require(ok, "ArcaneSettlement: USDC refund failed");

        emit RefundClaimed(marketId, msg.sender, refund);
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Admin
    // ─────────────────────────────────────────────────────────────────────────

    function setResolver(address newResolver) external onlyAdmin {
        require(newResolver != address(0), "ArcaneSettlement: zero address");
        resolver = newResolver;
        emit ResolverUpdated(newResolver);
    }

    function setAdmin(address newAdmin) external onlyAdmin {
        require(newAdmin != address(0), "ArcaneSettlement: zero address");
        admin = newAdmin;
        emit AdminUpdated(newAdmin);
    }

    function setDisputeWindow(uint256 marketId, uint256 windowSecs)
        external onlyAdmin marketExists(marketId)
    {
        require(windowSecs >= 3600, "ArcaneSettlement: window too short (min 1h)");
        markets[marketId].disputeWindowSecs = windowSecs;
    }

    // ─────────────────────────────────────────────────────────────────────────
    // View helpers
    // ─────────────────────────────────────────────────────────────────────────

    function getMarket(uint256 marketId)
        external view marketExists(marketId)
        returns (Market memory)
    {
        return markets[marketId];
    }

    function getUserShares(uint256 marketId, address user)
        external view marketExists(marketId)
        returns (uint256 yes, uint256 no)
    {
        return (yesShares[marketId][user], noShares[marketId][user]);
    }

    function getUserCollateral(uint256 marketId, address user)
        external view marketExists(marketId)
        returns (uint256)
    {
        return collateralDeposited[marketId][user];
    }

    function hasClaimed(uint256 marketId, address user)
        external view marketExists(marketId)
        returns (bool)
    {
        return claimed[marketId][user];
    }

    function contractUSDCBalance() external view returns (uint256) {
        return usdc.balanceOf(address(this));
    }
}
