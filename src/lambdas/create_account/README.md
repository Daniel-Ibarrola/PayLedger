# Account Creation Lambda

This dir contains the source code for the account creation lambda. This is a lambda triggered
by a cognito post confirmation event. This will create an account in the ledger database.

## Manual testing against AWS

`create_account` runs as a Cognito `post_confirmation` trigger (`infra/cognito.tf`), so
there's no way to invoke it directly with a representative event — it has to be exercised
by actually confirming a Cognito user.

### Look up the pool and client IDs

```bash
cd infra
terraform output
```

### Create and confirm a test user

```bash
aws cognito-idp sign-up \
  --client-id "3s10r8a9skb1j6kqf9pc13b2l9" \
  --username test-user@example.com \
  --password 'TempPass!2345' \
  --user-attributes Name=email,Value=test-user@example.com

# admin-confirm-sign-up bypasses the emailed code and fires post_confirmation immediately
aws cognito-idp admin-confirm-sign-up \
  --user-pool-id "us-east-2_nyqRJRTvc" \
  --username test-user@example.com
```

### Verify

```bash
aws logs tail /aws/lambda/payledger-dev-create-account --since 5m
```

The account row lands under the Cognito `sub`, not the email:

```bash
SUB=$(aws cognito-idp admin-get-user \
  --user-pool-id "$USER_POOL_ID" \
  --username test-user@example.com \
  --query "UserAttributes[?Name=='sub'].Value" --output text)

aws dynamodb get-item \
  --table-name payledger-ledger-table \
  --key "{\"PK\": {\"S\": \"ACCT#$SUB\"}, \"SK\": {\"S\": \"META\"}}"
```

Expect `current_balance` and `available_balance` both `0`.

### Clean up

Deleting the Cognito user does **not** touch the ledger row — remove both:

```bash
aws cognito-idp admin-delete-user \
  --user-pool-id "$USER_POOL_ID" \
  --username test-user@example.com

aws dynamodb delete-item \
  --table-name payledger-ledger-table \
  --key "{\"PK\": {\"S\": \"ACCT#$SUB\"}, \"SK\": {\"S\": \"META\"}}"
```
