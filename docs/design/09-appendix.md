# Appendix

## Known gaps — sections still to write

This doc is meant to read as a review-board document. Not yet present:

- **SLOs.** ADR-3 and the cost model both reason about p99 latency and cold-start tails against an unstated target.
- **Data retention.** Ledger entries, Aurora rows, and log groups all need a stated policy.
- **Regional failure behavior.** "Aurora could be rebuilt from DynamoDB" is asserted but no RTO/RPO is given.
- **Cost model — missing line items.** KMS is absent entirely and, with a customer-managed key on DynamoDB at this
  request volume, is potentially large enough to change the ordering below Step Functions. Cognito has no line
  either. (The EventBridge driver label has been corrected to match its figure.) Security now adds two small
  ones: the Secrets Manager secret ($0.40/month) and Cognito's Plus feature plan.

## Implementation plan

For the implementation plan see [roadmap.json](../roadmap.json).
