"""
contracts/ — On-chain integration layer for ArcaneSettlement.sol

Exports:
  - get_settlement_client(): Returns the initialized ArcaneSettlementClient singleton
  - ArcaneSettlementClient: The full contract wrapper class
"""
from .settlement import ArcaneSettlementClient, get_settlement_client

__all__ = ["ArcaneSettlementClient", "get_settlement_client"]
