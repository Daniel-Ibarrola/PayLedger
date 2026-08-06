# HTTP client requests

`authorization_service.http` runs against the deployed API from PyCharm's HTTP Client
(gutter ▶ per request, or "Run all requests in file").

Pick the `dev` environment from the dropdown before running. It needs two values, both
empty in the committed `http-client.env.json` and filled in by
`http-client.private.env.json`, which is **not** in git:

- `baseUrl` — written by `make http-env`, which reads the API URL from Terraform outputs.
- `idToken` — a Cognito JWT, obtained by hand (see below). Every route is behind the
  `cognito_auth` JWT authorizer (`infra/apigateway.tf`), so both requests 401 without it.

## Getting an `idToken`

The user pool client allows `USER_PASSWORD_AUTH` (`infra/cognito.tf`), so a username and
password exchange for tokens directly — no hosted UI needed. Using a test user you've
already signed up and confirmed in Cognito (see
`src/lambdas/create_account/MANUAL_TESTING.md`):

```bash
cd infra
terraform output -raw cognito_client_id

aws cognito-idp initiate-auth \
  --client-id 3s10r8a9skb1j6kqf9pc13b2l9\
  --auth-flow USER_PASSWORD_AUTH \
  --auth-parameters USERNAME=test-user@example.com,PASSWORD='TempPass!2345' \
  --query 'AuthenticationResult.IdToken' --output text
```

Paste the result into `idToken` in `http-client.private.env.json`. Use the **ID token**,
not the access token — the authorizer's `audience` is checked against the `aud` claim,
which only the ID token carries reliably.

Cognito ID tokens expire after an hour; re-run the command above and update the env file
when requests start 401ing.

## Why these values aren't committed

`baseUrl` isn't a credential, but on a public repo it's the only thing standing between
an outsider and the API before they'd need a valid token too — cheap to keep out of git.
`idToken` obviously can't be committed: it's a bearer credential for a real Cognito user.
