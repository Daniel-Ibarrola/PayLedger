# Expired Hold Sweeper Lambda

Scans for `PENDING` authorization holds whose `expires_at` has passed, restores the reserved
funds to the account's `available_balance`, and marks each hold `EXPIRED`. Runs on an
EventBridge Scheduler rule every 15 minutes (`infra/eventbridge.tf`) with a fixed input payload:

```json
{ "action": "expired_hold_cleanup", "source": "eventbridge_scheduler" }
```

The handler doesn't branch on that payload — it's carried through only so a CloudWatch Logs
search shows why the function ran. Any invoke, including an empty `{}` payload, runs the same
sweep.

## Manual testing against AWS

The deployed function name is `${project}-${environment}-expired_hold_sweeper`, i.e.
`payledger-dev-expired_hold_sweeper` for the default `dev` environment (`infra/variables.tf`).

### Invoke directly

No representative event is needed — the sweep doesn't read the input.

```bash
aws lambda invoke \
  --function-name payledger-dev-expired_hold_sweeper \
  --payload '{"action": "expired_hold_cleanup", "source": "eventbridge_scheduler"}' \
  --cli-binary-format raw-in-base64-out \
  response.json

cat response.json
```

Expect `{"status": "SUCCESS", "authorizations_expired": <N>}`. `N` is `0` unless there's an
already-expired `PENDING` hold in the table — see below to seed one.

### Watch it run

```bash
aws logs tail /aws/lambda/payledger-dev-expired_hold_sweeper --since 5m --follow
```

### Seed an expired hold to sweep

Holds expire 7 days after creation, so exercising the sweep against a freshly-placed hold means
waiting a week. It's faster to seed a hold directly with an `expires_at` already in the past,
against an existing test account (see `create_account/README.md` to create one).

```bash
ACCOUNT_ID="<sub-of-a-test-account>"
AUTH_ID="test-expired-hold-$(date +%s)"
NOW=$(date -u +%Y-%m-%dT%H:%M:%S.000000+00:00)
PAST_EXPIRY=$(date -u -d '8 days ago' +%Y-%m-%d)
AMOUNT=5000

aws dynamodb put-item \
  --table-name payledger-ledger-table \
  --item "{
    \"PK\": {\"S\": \"ACCT#$ACCOUNT_ID\"},
    \"SK\": {\"S\": \"AUTH#$NOW#$AUTH_ID\"},
    \"GSI1-PK\": {\"S\": \"AUTH#$AUTH_ID\"},
    \"GSI1-SK\": {\"S\": \"META\"},
    \"authorization_id\": {\"S\": \"$AUTH_ID\"},
    \"merchant_id\": {\"S\": \"merchant_test\"},
    \"amount\": {\"N\": \"$AMOUNT\"},
    \"status\": {\"S\": \"PENDING\"},
    \"created_at\": {\"S\": \"$NOW\"},
    \"updated_at\": {\"S\": \"$NOW\"},
    \"expires_at\": {\"S\": \"$PAST_EXPIRY\"},
    \"account_id\": {\"S\": \"$ACCOUNT_ID\"}
  }"

# Reserve the same amount on the account, mirroring what insert_authorization would have done,
# so the sweep's balance restore is actually visible.
aws dynamodb update-item \
  --table-name payledger-ledger-table \
  --key "{\"PK\": {\"S\": \"ACCT#$ACCOUNT_ID\"}, \"SK\": {\"S\": \"META\"}}" \
  --update-expression "SET available_balance = available_balance - :amt" \
  --expression-attribute-values "{\":amt\": {\"N\": \"$AMOUNT\"}}"
```

### Verify

```bash
# Hold flipped to EXPIRED
aws dynamodb get-item \
  --table-name payledger-ledger-table \
  --key "{\"PK\": {\"S\": \"ACCT#$ACCOUNT_ID\"}, \"SK\": {\"S\": \"AUTH#$NOW#$AUTH_ID\"}}"

# available_balance back to what it was before the update-item above
aws dynamodb get-item \
  --table-name payledger-ledger-table \
  --key "{\"PK\": {\"S\": \"ACCT#$ACCOUNT_ID\"}, \"SK\": {\"S\": \"META\"}}"
```

### Clean up

```bash
aws dynamodb delete-item \
  --table-name payledger-ledger-table \
  --key "{\"PK\": {\"S\": \"ACCT#$ACCOUNT_ID\"}, \"SK\": {\"S\": \"AUTH#$NOW#$AUTH_ID\"}}"
```
