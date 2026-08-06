# Security

## Authentication

Authentication is done via Cognito Authorizer. Cognito stable user id (`sub`) will map directly to the 
account_id as we won't support multiple accounts per user. The Cognito user pool will enforce a strict password
policy consisting of minimum 12 characters, at least one uppercase letter, one lowercase letter, one number, 
and one special character, and use of MFA

## Authorization (access control)

Terminology note: this subsection is about *access control*. Elsewhere in this document "authorization" means a
card hold, and the "Authorization Service" is the Lambda that places one. They are unrelated.

The Cognito authorizer establishes **who** the caller is. It says nothing about **what** they may act on, and that
gap is the whole of this subsection: without an ownership rule, any authenticated user could read another user's
balance or place a hold against their account.

**The rule: `account_id` is derived from the token, never from the request.**

It is not read from the request body, and not read from the path. It is the validated `sub` claim, and nothing
else. The API shape enforces this rather than relying on a check:

- `POST /authorizations` takes no `account_id` field. The hold is always placed against the caller's own account.
  A request that includes an `account_id` is rejected with 400 rather than ignored, so a client built against the
  wrong assumption fails loudly instead of silently operating on the caller's account.
- Balance and history are addressed as `/accounts/me/...`. There is no path variable to tamper with.

This matters more than the equivalent check would. A validation rule is something every new endpoint has to
remember; a shape with nowhere to put the wrong account is one where the mistake cannot be expressed. The
alternative — keeping `/accounts/{id}/...` and asserting `id == sub` in each handler — is functionally equivalent
and structurally worse, because it is one forgotten line away from an IDOR on any endpoint added later.

**Ownership on authorization-scoped routes.** `POST /authorizations/{id}/capture` and `/void` are addressed by
authorization id, which is not derivable from the token. These handlers load the authorization and reject it unless
its `account_id` equals `sub`. The rejection is a **404, not a 403** — a 403 confirms that the id exists, which
turns the endpoint into an oracle for enumerating other users' authorization ids.

**Caller model: cardholder only (ADR-7).** There is exactly one authenticated caller class, and it is the
cardholder. This is what makes everything above work as a *shape* rather than as a rule: with a single caller
class, `account_id == sub` is the entire authorization model, and there is nowhere in the API to express a
different account. Merchants appear throughout the data model and the ledger as counterparties, but they have no
Cognito identity, no scopes, and no ability to act — the merchant side of a capture is written by the system on the
cardholder's behalf.

This diverges from real card flows, which are merchant-initiated, and the divergence is deliberate rather than an
oversight — see ADR-7 for the reasoning and for what adding a merchant caller class would cost.

**The one exception, stated as an exception: `POST /merchants`.** It is the only route that does not act on the
caller's own account, and it has no ownership rule at all — any authenticated cardholder can create a merchant, and
the result belongs to nobody. Everything above is therefore a claim about the *account-scoped* routes, which is
still every route that touches money. Merchant creation is protected by a conditional write rather than by the
API's shape (see IAM), and the reasoning for accepting that is in ADR-7.

The practical consequence for this section: any *further* endpoint that cannot be expressed as "the caller acting
on their own account" is a signal that the single-axis model is being outgrown, and it should go through ADR-7
rather than acquiring a bespoke ownership check. One exception is a documented trade; two is a second
authorization axis that has not been designed.

## IAM

Least privilege is applied **per function**, not per service: every Lambda gets its own role, and no role is shared.
The default posture is that a role can perform exactly the operations its handler makes, on exactly the resources it
names.

Two conventions used throughout:

- **Resources are ARNs, never `*`.** DynamoDB policies name the table ARN, and separately name
  `…:table/payledger/index/GSI1` where the handler queries the GSI — a table-only policy silently fails every index
  query, which is the failure mode to design out rather than debug.
- **Every role that touches DynamoDB also needs KMS.** The table uses a customer-managed key, so encryption is
  transparent to the code but not to IAM. Those roles carry `kms:Decrypt` and `kms:GenerateDataKey` on the key,
  conditioned with `kms:ViaService: dynamodb.<region>.amazonaws.com` so the key cannot be used for anything else.

| Role | Actions | Resource |
|---|---|---|
| Merchant Service | `dynamodb:PutItem` | Table, `LeadingKeys` conditioned to `MERCHANT#*` — no `GetItem`, no `Query`, no index |
| Create Account (Cognito Post Confirmation trigger) | `dynamodb:PutItem` | Table, `LeadingKeys` conditioned to `ACCT#*` — no `GetItem`, no `Query`, no index; mirrors Merchant Service's write-only, condition-guarded pattern |
| Authorization Service | `dynamodb:PutItem`, `UpdateItem`, `GetItem`, `Query` | Table + GSI1 |
| Balance Service | `dynamodb:GetItem` | Table only — no `Query`, no index |
| Transaction History Service | `rds-db:connect` | `dbuser:<proxy-id>/<read-only-user>` |
| EventBridge Pipe (Streams → bus) | `dynamodb:GetRecords`, `GetShardIterator`, `DescribeStream`, `ListStreams`; `events:PutEvents`; `sqs:SendMessage` for the source DLQ | Stream ARN; bus ARN; DLQ ARN. Trusts `pipes.amazonaws.com`, not `lambda.amazonaws.com` |
| Aurora projector | `sqs:ReceiveMessage`, `DeleteMessage`, `GetQueueAttributes`, `ChangeMessageVisibility`; `rds-db:connect` | Queue ARN; `dbuser:<proxy-id>/<writer-user>` |
| Expired-hold sweeper | `dynamodb:Query`, `UpdateItem` | GSI (expiry index) for read; table for write |
| FraudScreen | `dynamodb:Query` | Table + GSI1 — **read only** |
| SubmitSettlement | `secretsmanager:GetSecretValue` | The acquirer secret's ARN — **no DynamoDB access at all** |
| Acquirer secret rotator | `secretsmanager:DescribeSecret`, `GetSecretValue`, `PutSecretValue`, `UpdateSecretVersionStage`; `secretsmanager:GetRandomPassword` | The acquirer secret's ARN; `GetRandomPassword` takes no resource and is granted on `*` because it has none |
| NotifyCustomer | `sns:Publish` | Topic ARN |
| CompensateLedger | `dynamodb:PutItem`, `UpdateItem`, `DeleteItem` (scoped, below) | Table |
| Step Functions execution role | `lambda:InvokeFunction`; log delivery (below) | The four task Lambda ARNs, listed individually |
| EventBridge rule target role | `states:StartExecution`, `sqs:SendMessage` | State machine ARN; queue ARN |
| DLQ replay tool (operator) | `sqs:ReceiveMessage`, `DeleteMessage`, `SendMessage`, `GetQueueAttributes`, `StartMessageMoveTask`, `ListMessageMoveTasks` | DLQ + source queue ARNs |
| Terraform CI role | Deploy-time only; `iam:PassRole` scoped to the execution role ARNs above. **No DynamoDB data-plane access at all** (below) | — |

Baseline on every Lambda: `logs:CreateLogGroup`, `CreateLogStream`, `PutLogEvents`, plus `xray:PutTraceSegments`
and `PutTelemetryRecords`. Anything reaching Aurora through RDS Proxy additionally needs the VPC ENI permissions
(`ec2:CreateNetworkInterface`, `DescribeNetworkInterfaces`, `DeleteNetworkInterface`). Powertools' EMF metrics need
**no** IAM permission — they are emitted as structured log lines, so `cloudwatch:PutMetricData` is a reflex to
resist. The Pipe is the one row that baseline does *not* apply to: it is not a Lambda, so it gets no X-Ray
permissions, and its logging is a property of the pipe (log destination plus log level) rather than something the
runtime does on its own — it needs `logs:CreateLogStream` and `PutLogEvents` on its own explicitly-created log
group.

**The append-only ledger is enforced in IAM, not just in code.** ADR-5 says posted entries are never mutated or
deleted. Every role above except `CompensateLedger` carries an explicit `Deny` on `dynamodb:DeleteItem`, which
means a bug or a hotfix cannot delete a ledger entry even if someone writes the call. `CompensateLedger` is the
sole exception because reversal must delete the capture's idempotency record (OQ-9), and its permission is scoped
by key prefix so it can delete *only* that:

```
Allow   dynamodb:DeleteItem
        Condition: StringLike { "dynamodb:LeadingKeys": ["IDEM#*"] }
Deny    dynamodb:DeleteItem  on everything else
```

Ledger entries live under `ACCT#` and `MERCHANT#` partition keys, so the condition makes deleting one impossible
for every principal in the system. This is the strongest single control in the design: the core domain invariant is
enforced by the platform rather than trusted to application code.

**Merchant creation does not get an exception to this, which is why there is no delete endpoint.** The obvious way
to let an operator remove a merchant is `DeleteItem` conditioned on `LeadingKeys: ["MERCHANT#*"]` — and that would
blow a hole straight through the control above. `dynamodb:LeadingKeys` constrains the **partition** key only; there
is no equivalent condition key for the sort key. A merchant's identity item (`MERCHANT#<id>` / `META`) and its
ledger entries (`MERCHANT#<id>` / `TXN#...`) share a partition, so any policy that can delete the first can delete
the second, and IAM cannot tell them apart. The Merchant Service therefore carries the same `DeleteItem` deny as
everything else, merchant removal is a table-level operation (destroy and re-create) rather than an item-level one,
and the API surface reflects that rather than papering over it.

**Overwrite is the hazard IAM cannot cover, and it is the reason the create is conditional.** An unconditional
`PutItem` on an existing `merchant_id` silently resets that merchant's `payable_balance` to `0`, and the Merchant
Service is reachable by any authenticated caller who guesses or reuses an id — that is a corruption of
ledger-adjacent state through the front door, not a deploy-time accident. For the same sort-key reason as above, no
policy can distinguish "create this merchant" from "overwrite this merchant," so IAM cannot be the control here.

The control is the handler's `ConditionExpression: attribute_not_exists(PK)`. This is one of the few places in the
design where a core protection lives in code rather than in the platform, and it is called out in the section that
otherwise argues for platform enforcement precisely because it is the exception: it needs a test asserting that a
second create leaves `payable_balance` untouched, since nothing below the application layer will catch a
regression.

Two narrowings limit what a bug in that handler can reach. Its `PutItem` is conditioned to `LeadingKeys:
["MERCHANT#*"]`, so the blast radius stops at merchant items — it cannot touch an account, an authorization, or an
idempotency record. And it gets **no `GetItem`**: the create path never reads, because the conditional write *is*
the existence check, and the `UnknownMerchant` lookup on `POST /authorizations` belongs to the Authorization
Service's role rather than this one.

**No deploy-time credential touches table data.** The Terraform CI role has no DynamoDB data-plane permissions at
all. Merchant data is created through the API like everything else, so a deploy role has nothing to write and no
reason to hold a key to the table.

**Note the two places transactions and IAM interact.** There is no `dynamodb:TransactWriteItems` IAM action —
transactional writes are authorized through the underlying `PutItem` / `UpdateItem` / `DeleteItem` permissions, so
the Authorization Service's policy grants those rather than naming the API it calls. And because the deny above
applies inside transactions too, a transaction containing a `Delete` on a ledger entry fails authorization as a
whole rather than partially applying.

**The one unavoidable wildcard.** ADR-2 makes Express execution logging mandatory, and the log-delivery permissions
that requires — `logs:CreateLogDelivery`, `GetLogDelivery`, `UpdateLogDelivery`, `DeleteLogDelivery`,
`ListLogDeliveries`, `PutResourcePolicy`, `DescribeResourcePolicies`, `DescribeLogGroups` — only function with
`Resource: "*"`. This is an AWS constraint, not an oversight. It is confined to the Step Functions execution role,
which holds no data-plane permissions, so the blast radius is log delivery configuration and nothing else.

**Deriving the real list.** These are the permissions the design implies. The permissions the system actually uses
should be generated from CloudTrail with IAM Access Analyzer policy generation, after week 3's chaos testing has
exercised the rarely-taken paths — compensation and DLQ redrive — since anything never invoked will be absent from
a generated policy.

## Network boundary

The Aurora section's **Network path out of the VPC** covers *routing* — what can be reached, and why an interface
endpoint is the only way out. This subsection covers *enforcement*: the addresses, the security groups, and what is
deliberately left at its default.

**There are no public subnets.** The VPC has no internet gateway at all, so "private subnet" is a structural fact
rather than a naming convention — a resource cannot be accidentally exposed by a misapplied route table, because
there is no route to apply. This is the same instinct as `/accounts/me`: make the mistake unrepresentable rather
than forbidden.

| Block | CIDR | Contents |
|---|---|---|
| VPC | `10.0.0.0/16` | — |
| Private subnet A | `10.0.1.0/24` | Interface endpoint ENIs, Lambda ENIs, Aurora writer — everything billable |
| Private subnet B | `10.0.2.0/24` | Empty by design; exists only to satisfy the two-AZ requirement |

Subnet B holds nothing. Aurora's DB subnet group and RDS Proxy both refuse to create with subnets in fewer than two
AZs, and subnets themselves are free, so B is declared and left unused — the single-AZ decision and its reasoning
are in **AZ span: one, deliberately**. A `/16` and `/24`s are far larger than needed; the addresses cost nothing and
a cramped CIDR is the kind of thing that is painful to widen later.

**Security groups, referenced by id and not by CIDR.** Four groups, each naming the *other group* as its peer rather
than an address range. A CIDR rule admits anything that happens to land in that range; an SG-to-SG rule admits
exactly the resources carrying that group, and stays correct when subnets change.

| Group | Attached to | Ingress | Egress |
|---|---|---|---|
| `lambda-sg` | Aurora projector, Transaction History Service | *none* | `5432` → `proxy-sg`; `443` → `endpoint-sg` |
| `endpoint-sg` | SQS and X-Ray interface endpoint ENIs | `443` from `lambda-sg` | *none* |
| `proxy-sg` | RDS Proxy | `5432` from `lambda-sg` | `5432` → `aurora-sg` |
| `aurora-sg` | Aurora cluster | `5432` from `proxy-sg` | *none* |

Two properties worth stating because they are the point of the table:

- **Aurora admits the proxy and nothing else.** `lambda-sg` is not in `aurora-sg`'s ingress, so a function that
  hardcodes the cluster endpoint instead of the proxy endpoint fails to connect rather than quietly bypassing the
  connection pooling that the Aurora section exists to establish. The architectural rule is enforced by the network,
  not by code review.
- **No group has `0.0.0.0/0` egress.** Terraform's `aws_security_group` revokes the default allow-all egress rule
  when egress blocks are specified, so egress here is genuinely an allowlist. This matters more than usual given the
  no-NAT design: a function with unrestricted egress and no route still fails, but it fails as a hang at the end of
  the socket timeout, whereas a function denied at the security group fails immediately and legibly.

**The default security group is emptied.** A VPC's default group ships permitting all traffic between members, and
anything created without an explicit group lands in it. `aws_default_security_group` with no rule blocks is
declared purely to strip it, so the permissive path does not exist even for a resource added carelessly later.

**Network ACLs are left at their default allow-all, deliberately.** NACLs are stateless, which means a hand-written
one has to permit the ephemeral return port range explicitly, and the failure mode when it does not is
indistinguishable from the no-NAT hang above. The security groups are stateful and already express every rule in
the table; a second, subtler layer restating the same policy adds a way to be wrong without adding a control.

**VPC Flow Logs are enabled on the VPC** to a log group with 7-day retention, capturing `ALL` rather than only
`REJECT`. The reason is the failure mode the Aurora section calls out: a missing endpoint or route produces a hang,
and a hang leaves no `REJECT` record because nothing rejected it. What it does leave is a flow with bytes out and no
return flow, which is exactly the shape a full flow log shows and exactly what a Lambda timeout does not tell you.

## Encryption at rest

**One customer-managed key, `alias/payledger`, symmetric, with automatic annual rotation enabled.** A single CMK
across every store is a deliberate simplification: separate keys per service would let one store's key be revoked
independently, which is a property this system has no use for, and would multiply the `$1/month/key` charge and the
number of key policies to get right.

Rotation here means AWS generates new backing key material each year while the key id and ARN stay fixed; prior
material is retained, so ciphertext written before a rotation still decrypts and there is no re-encryption step.
It is a checkbox with no migration attached, which is why it is on.

| Store | Encrypted with | Note |
|---|---|---|
| DynamoDB table | CMK | Covers the table, its indexes, its backups, and the Stream's records |
| SQS queues and DLQs | CMK | Consumers need `kms:Decrypt`; producers need `kms:GenerateDataKey` |
| SNS topic | CMK | — |
| Aurora cluster | CMK | Set at creation, **immutable** |
| Automated backups, snapshots, PITR | CMK | Inherited; cannot diverge from the cluster |
| Secrets Manager secret | CMK | See Secrets management |
| CloudWatch log groups | CMK | Requires a key policy statement — below |
| Lambda environment variables | CMK | Encrypted at rest, which is *not* the reason they hold no secrets |

**Aurora's key cannot be changed after creation.** Unlike DynamoDB — which can be moved between an AWS-owned key, an
AWS-managed key, and a CMK at any time — an unencrypted or wrongly-keyed Aurora cluster is fixed only by snapshot,
copy-the-snapshot-with-the-new-key, restore. Getting this right in the Terraform that first creates the cluster is
therefore not a detail, and it is worth knowing that the two stores behave differently rather than assuming the
DynamoDB behaviour generalises.

**Log groups need an explicit grant to the logs service principal.** `AssociateKmsKey` fails outright without it,
and the failure surfaces at apply time as a permissions error on a resource that looks like it should just work:

```
Principal: logs.<region>.amazonaws.com
Action:    kms:Encrypt*, kms:Decrypt*, kms:ReEncrypt*, kms:GenerateDataKey*, kms:Describe*
Condition: ArnLike { "kms:EncryptionContext:aws:logs:arn":
             "arn:aws:logs:<region>:<account>:log-group:*" }
```

The encryption-context condition is what keeps this from being a general grant: the logs service can use the key
only when encrypting for a log group in this account.

**The `ViaService` conditions follow the store, not the caller.** The IAM section's convention — `kms:ViaService`
pinned so a role's key access cannot be repurposed — needs the *right* service on each role. The Pipe reading
DynamoDB Streams uses `dynamodb.<region>.amazonaws.com` even though it is not calling the table API; the projector
receiving from SQS uses `sqs.<region>.amazonaws.com`. Copying the DynamoDB condition onto a queue consumer produces
a role that is denied at runtime for reasons the policy text makes look correct.

**Why this is a small cost line and not a per-request one.** DynamoDB uses envelope encryption with a table-level
data key that it caches, so a CMK on the table is not a KMS call per item — which is what makes a customer-managed
key affordable at this request volume at all. The exact figure belongs to the cost model's open KMS line item and
is not resolved here; the mechanism is stated so that whoever closes that gap does not price it per-request.

## Encryption in transit

**The public edge has no TLS configuration, and that is the finding.** API Gateway enforces a fixed `TLS_1_2`
security policy on HTTP APIs — TLS 1.2 and 1.3 accepted, everything older rejected. The `security_policy` setting
that exists for REST APIs is a property of *custom domain names*, and for HTTP APIs it accepts only `TLS_1_2`
anyway. There is no minimum-TLS knob to set, no weak default to harden, and nothing here for a review to flag.

This build uses the default `execute-api` endpoint with the AWS-managed certificate. A custom domain would add an
ACM certificate (free) and a Route 53 hosted zone ($0.50/month), and would introduce a real trap:
`disable_execute_api_endpoint` must be set, or the default endpoint keeps serving traffic alongside the custom
domain and every control attached to the domain is bypassable by calling the original hostname.

**Internal legs.** Every AWS SDK call is HTTPS by default; SigV4 authenticates and integrity-protects a request but
does not encrypt it, so TLS is doing the confidentiality work on all of them. Traffic from a VPC-attached Lambda to
an interface endpoint is TLS terminated at the PrivateLink ENI, so it is encrypted *and* never leaves the AWS
network. EventBridge, SQS, SNS, DynamoDB Streams, and the Cognito token endpoint are all HTTPS-only with no plaintext
option to disable.

**The Aurora leg is the one that needs a decision.** RDS Proxy is configured with `require_tls = true`, so a client
that omits TLS is rejected at connection time rather than silently downgraded — the important half, because a
downgrade is invisible from the application side. The proxy-to-cluster leg is likewise TLS.

The remaining gap is on the client: the connection string uses **`sslmode=verify-full`**, not `sslmode=require`.
`require` encrypts but authenticates nothing, which leaves it satisfied by any certificate at all; `verify-full`
checks the chain against the RDS CA bundle and checks the hostname. The bundle ships alongside the layer's Python
as a data file, so it does not disturb the pure-first-party-Python packaging that `infra/layers.tf` depends on —
though the Postgres driver the projector needs will, and that is already flagged as the trigger for a real build
step.

## Secrets management and rotation

There is exactly one secret in the system: the acquirer credential read by `SubmitSettlement`.

**It is not a Lambda environment variable.** Environment variables are readable by anyone holding
`lambda:GetFunctionConfiguration`, are rendered in the console, and are returned by a plain `GetFunction` — so an
otherwise-harmless read-only role becomes a credential disclosure. Secrets Manager makes reading the value a
distinct, separately-grantable, CloudTrail-logged action, which is the property being bought.

**Terraform holds a placeholder, never the value.** An `aws_secretsmanager_secret_version` with a real secret in it
puts that secret in Terraform state in plaintext — the state bucket is encrypted, but the blast radius of state
access should not include production credentials. So Terraform creates the secret and a placeholder version, and
`lifecycle { ignore_changes = [secret_string] }` keeps subsequent applies from reverting the rotated value back to
the placeholder. That `ignore_changes` is not optional; without it, every `terraform apply` silently breaks
settlement.

**Reads are cached per execution environment.** `GetSecretValue` costs $0.05/10k calls and the secret itself $0.40/
month, so an uncached read on every invocation is both a Secrets Manager and a KMS charge per request. The value is
fetched at cold start and held in a module global (or via Powertools' `parameters` utility with a TTL) — with the
consequence noted under rotation below.

**Rotation: the four steps, and what each one means.** Secrets Manager invokes a rotation Lambda four times per
rotation, passing a step name and a version id:

| Step | What it does |
|---|---|
| `createSecret` | Generate a new value and store it labelled `AWSPENDING` |
| `setSecret` | Push the pending value to the counterparty that must accept it |
| `testSecret` | Authenticate with the pending value to prove it works |
| `finishSecret` | Move `AWSCURRENT` to the new version; the old one becomes `AWSPREVIOUS` |

The staging labels are the whole mechanism. `AWSCURRENT` — the label every consumer reads — moves only after
`testSecret` has passed, so a consumer can never be handed a value that was never verified. A rotation that fails at
`setSecret` or `testSecret` leaves `AWSCURRENT` untouched and the system running on the old credential, which is the
correct failure direction.

**The failure this design has to handle is caching, not rotation.** Because the secret is cached for the life of an
execution environment, `finishSecret` does not reach a warm Lambda — it keeps presenting the old credential until
its environment is recycled. `SubmitSettlement` therefore treats an authentication failure as a signal to invalidate
its cache, re-read `AWSCURRENT`, and retry once, rather than as a terminal error. Without that, every rotation
produces a burst of settlement failures that heal on their own after some unpredictable interval — the kind of
incident that is miserable to diagnose precisely because it recovers before anyone finishes looking at it.

**The rotator is a stub, and this is stated rather than disguised.** The acquirer is simulated — the Step Functions
section's `SubmitSettlement` raises a synthetic `SettlementTimeoutError` and there is no counterparty behind it. So
`setSecret` has nobody to push to and `testSecret` has nothing to authenticate against; both are implemented as
logging no-ops. All four steps, the staging-label transitions, and the schedule are real.

What that genuinely exercises: the rotation Lambda's resource policy allowing the `secretsmanager.amazonaws.com`
service principal to invoke it (scoped with `SourceArn` to this one secret, or any secret in the account can trigger
it), the 30-day rotation schedule, the label transitions, and — most valuable — the consumer's cache-invalidation
path, which is where the real bug lives. What it does not exercise is the counterparty handshake, which is the only
part a real integration adds.

`rotate_immediately` is set to `false`, or every `terraform apply` triggers a rotation as a side effect of an
unrelated change.

## Abuse controls and rate limiting

**AWS WAF is not used in this project, and the first reason is that it cannot be.** WAF associates with CloudFront
distributions, API Gateway **REST** APIs, ALBs, AppSync, Cognito user pools, App Runner, Bedrock AgentCore Gateway,
Verified Access, and Amplify — HTTP APIs are not on the list. "Put WAF in front of the API" is therefore not a
configuration change here; it is either a CloudFront distribution in front of the API with the web ACL on the
distribution, or a migration back to a REST API. Both are real architectural changes with real costs, and neither is
worth making for this project.

This is worth stating plainly because the HTTP API was chosen for cost and simplicity, and losing WAF association
is a consequence of that choice that would otherwise be discovered at the point someone tried to configure it.

**Where the abuse surface actually is.** Every API route requires a valid Cognito token, so an unauthenticated
attacker cannot reach the API at all — the reachable surface is the user pool's sign-up and sign-in endpoints.
Credential stuffing, enumeration, and sign-up flooding hit Cognito, not API Gateway.

**Cognito user pools *are* a supported WAF target, and that option is declined too.** It is the one place a web ACL
could be attached without an architectural change, so the decision is worth being explicit about rather than leaving
implied by the gap above. A web ACL bills $5/month plus $1/month per rule plus $0.60 per million requests, and
Account Takeover Prevention — the managed group actually aimed at credential stuffing — is a further $10/month plus
per-attempt charges. Against a $10–30 total budget, a user pool with a handful of test accounts, and no public
sign-up traffic to speak of, the managed rule groups defend against a threat model this project does not have.
Cognito's own per-pool sign-in rate limiting and user existence errors (control 3 below) cover the reachable
surface. WAF is named here so its absence reads as a decision, not an oversight; on a real user base with open
sign-up it is the first control to add back.

The controls that *are* built, outermost first:

**1. API Gateway throttling — a cost control first, a capacity control second.** The stage sets a default route
throttle well below the account default of 10,000 rps / 5,000 burst, with `POST /authorizations` tightened further.
The reasoning is specific to this project: nothing here needs thousands of requests per second, but a runaway test
loop or a leaked token *can* generate them, and at 10,000 rps the Lambda and DynamoDB charges would blow through a
$10–30 budget in minutes. Throttling is the control that bounds the bill.

The limitation to know: HTTP APIs support stage-level and per-route throttling but **not usage plans or API keys**,
which are REST-only. There is no per-caller quota mechanism at the gateway, so gateway throttling protects the
*backend*, not a *tenant* — one abusive account can consume the whole stage limit and throttle everyone else.
Per-account fairness would need a token bucket in DynamoDB in the handler, and is not built; with a single caller
class and a handful of users it is a theoretical fairness problem, and it is named so it is not mistaken for a
solved one.

**2. Reserved concurrency as the hard stop.** Each function gets reserved concurrency sized to its expected load.
It is free, and it bounds the blast radius even if the gateway throttle is misconfigured or bypassed — the two
controls fail independently, which is the only reason to have both. The trade-off is that reserved concurrency also
caps legitimate bursts and sheds the excess as throttles, which the error contract already surfaces as `429`.

**3. Cognito's own controls.** Sign-in attempts are rate-limited by Cognito per user pool regardless of
configuration. Beyond that, **user existence errors are enabled**, so a failed sign-in returns the same generic
error whether or not the username exists — the same reasoning that makes an authorization owned by another account a
`404` and not a `403`, applied at the authentication endpoint. Threat protection (compromised-credential detection,
adaptive authentication) requires the **Plus** feature plan at $0.02/MAU with no free tier, against Essentials at
$0.015/MAU with 10,000 free MAUs. At this project's user count the difference is cents and Plus is worth it for the
authentication event log alone; on a real user base it is a per-MAU decision rather than a checkbox, which is the
part worth remembering.

**4. Idempotency keys, which are an abuse control as well as a correctness one.** A replayed capture cannot
double-post — the second request returns the stored `response_snapshot`. This is the control that makes the
difference between a request flood being an availability and cost problem versus a *financial* one.

**5. `POST /merchants` is the one unbounded-growth surface, and it is bounded by the controls above rather than by
anything specific to it.** It is the only route where an authenticated caller can create rows without limit and
without spending a balance — every other write is gated by an account's funds or by an existing authorization.
A script looping on it produces junk merchants and DynamoDB write charges, so the defences are the stage throttle,
the function's reserved concurrency, and the budget alarm below. What it cannot do is any financial damage: an
existing merchant cannot be overwritten (the conditional write), and a merchant with no authorizations against it
moves no money. Per-caller quotas would be the real fix and are not available at the gateway on an HTTP API.

**6. An AWS Budgets alarm at $20**, plus a CloudWatch alarm on aggregate Lambda invocation count. Every control
above can be misconfigured; this is the one that reports it. For a learning project with a hard budget it is
realistically the most valuable line in this subsection.

## Audit logging

Three planes, three different mechanisms, and the useful observation is that the domain plane is already solved by a
decision made for other reasons.

**Control plane — CloudTrail.** A multi-region trail with **log file validation enabled**, delivering to a
dedicated S3 bucket encrypted with the CMK, public access blocked, versioning on. Management events on the account's
first trail are free, which makes this the cheapest meaningful control in the document. It answers: who changed an
IAM policy, who read the acquirer secret, who used the KMS key and for what, who deleted the stack.

Log file validation is what separates an audit trail from a log — CloudTrail writes signed digest files, so
after-the-fact tampering with delivered logs is detectable. A log an attacker can quietly edit answers no question
worth asking.

**Domain plane — the append-only ledger and DynamoDB Streams.** Every state change to the table appears in the
stream with old and new images, and ADR-5's immutability is enforced in IAM, so posted entries cannot be altered or
deleted by any principal in the system. The table *is* the audit record for domain events: the state at any past
moment is the entries up to that point, and a correction is a new visible entry rather than an edit that hides the
original. This is the strongest audit property in the design and it was bought as a correctness decision, not a
security one.

Two limits to be precise about. **Streams retain 24 hours** — the stream is a transport, not an archive; the durable
record is the table itself plus point-in-time recovery. And **Streams capture writes, not reads**: "who looked at
this balance" is unanswerable from the stream.

**That read gap is what CloudTrail data events would close, and they are not enabled.** Item-level DynamoDB data
events bill at ~$0.10 per 100,000 events and would be dominated by the system's own reads — every balance check,
every idempotency lookup, every saga step. At this project's request volume that would plausibly become a top-three
cost line, which is a strange outcome for a $10–30 budget. It is documented here as the production upgrade, with the
note that in production it is usually scoped to a subset of tables rather than enabled wholesale.

**Application plane.** Structured logs correlated on the request id, Step Functions Express execution logging, and
X-Ray. These are covered under PII and data classification below, and it is worth noticing why the two subsections
overlap: the audit trail and the leak path are the same pipes. Anything added to make an event more auditable also
makes it more disclosive, which is why the rule there is explicit fields rather than whole bodies.

Cognito authentication events — who signed in, from where, and with what risk assessment — are available through
threat protection's event log and `AdminListUserAuthEvents`, and are another thing gated behind the Plus feature
plan discussed above.

## PII and data classification

Cognito holds the smallest possible authentication footprint (email/phone + password hash), the data stores hold
account and financial data under an owned KMS key, and the two are joined only by an opaque `sub`. Everything below
follows from keeping that split intact.

**No card data ever enters this system.** There is no PAN, no CVV, no expiry date, no cardholder name — not in the
data models, not in a request body, not in transit. An "authorization" here is a hold against an internal account
balance, not a message to a card network. This is the most important sentence in the section: it places the system
entirely **outside PCI DSS scope**, and it is a property to defend deliberately rather than one to rediscover later.
If a real card reference is ever needed, it arrives as a network token from a vault the system does not own, and the
token — never a PAN — is what gets stored.

**What is held, and where**

| Data | Classification | Store | Protection |
|---|---|---|---|
| Email, phone, password hash | Direct identifiers | Cognito **only** | Managed by Cognito; never copied into DynamoDB or Aurora |
| `account_id` (= `sub`) | Pseudonymous identifier | DynamoDB, Aurora, logs | Opaque; resolves to a person only via Cognito |
| Amounts, timestamps, `merchant_id` | Sensitive financial data | DynamoDB, Aurora | CMK at rest, TLS in transit |
| `merchant.name` | Business data, **caller-supplied** | DynamoDB, Aurora, logs | Not personal data; untrusted input — length-bounded and character-restricted at the API boundary |
| `response_snapshot` | Copy of a response body | DynamoDB (idempotency records) | CMK at rest; TTL-bounded to 24–48h |

The fourth row is the only caller-supplied string in the table, and the classification is doing less work than it
looks. `merchant.name` is not personal data, but it is arbitrary text from an authenticated stranger that lands in
DynamoDB, is replicated into Aurora by the projector, and appears in structured logs — three stores, none of which
validate it. Bounding length and characters at the API boundary is the whole of the protection. `merchant_id` is
subject to the same argument and additionally becomes a partition key, so an allow-list on it is a data-model
concern as well as a hygiene one.

The row that gets underrated is the third. A transaction set carries no name and no email, and is still sensitive:
merchant plus amount plus timestamp is a spending profile, and a spending profile is disclosive on its own. That is
what justifies encryption and retention limits on the financial data, not just on the credentials — and it is why
pseudonymity is a mitigation here rather than an exemption.

**Aurora holds a second copy of the financial data.** The projector replicates ledger entries out of DynamoDB, so
the protections above have to hold in two places, not one:

- Encrypted at rest under the CMK and reached only over TLS through RDS Proxy — see Encryption at rest and
  Encryption in transit, which also cover why the cluster's key is fixed at creation.
- Network-isolated in private subnets with no public accessibility, admitting the proxy and nothing else — see
  Network boundary for the security group rules that enforce it.
- Queried with **parameterized statements**, so account ids and amounts never appear as literals in query text.
  This matters more than it looks: the runbook's Aurora procedure sends an operator to Performance Insights to read
  top SQL by load, and inlined literals would put customer data on that screen.
- Automated backups and snapshots inherit the cluster's encryption.

Being a derived store cuts both ways. It is a second copy to protect — but because it is disposable and rebuildable
from DynamoDB, it is also the copy that can simply be dropped and reprojected, which is what makes the erasure story
below tractable.

**Logs are the leak path, and this design has three of them.** Structured logging and tracing will capture whatever
they are handed, so the rule is that request and response bodies are never logged whole:

- **Application logs.** Powertools' `Logger` logs explicit fields only — never the raw event, never the response.
- **X-Ray.** No identifiers in annotations. Annotations are indexed and searchable, which makes them the worst
  place to put an `account_id`; correlation happens on the request id instead.
- **Step Functions execution logging.** This is the design-specific one. ADR-2 makes Express execution logging
  mandatory and sets log level `ALL` *including execution data* — which means the payload passed between saga
  states lands in CloudWatch Logs by design. That payload carries `account_id`, amount, and `merchant_id`. The
  mitigation is to keep the saga payload minimal (ids and a decision, not a copy of the record) and to accept that
  this log group holds sensitive financial data and must be scoped, retained, and access-controlled accordingly.

As a backstop rather than a primary control, a **CloudWatch Logs data protection policy** with managed data
identifiers masks email addresses and phone numbers if one ever reaches a log group. It is a safety net for a
mistake, not a substitute for not making it.

**Erasure versus an append-only ledger.** These are in genuine conflict and the conflict has to be resolved
explicitly rather than hand-waved. ADR-5 makes posted ledger entries immutable and IAM enforces it, so a deletion
request *cannot* be satisfied by deleting financial records — and should not be, since retaining them is a legal
obligation in its own right.

The resolution is to delete the identity and keep the pseudonymous record:

1. Delete the Cognito user — email, phone, and password hash go, and with them the only mapping from `sub` to a
   person.
2. Retain ledger entries, authorizations, and balances keyed by `sub`, under financial record-keeping retention.
3. Drop and reproject the affected rows in Aurora, since it is derived and holds no authority.

What remains afterward is a set of amounts and timestamps attached to an identifier that no longer resolves to
anyone. The ledger stays balanced and auditable, and the personal data is genuinely gone. The retention periods
themselves belong in the data-retention section, which is still to be written.
