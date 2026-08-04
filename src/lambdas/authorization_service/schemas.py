from pydantic import BaseModel, ConfigDict, Field


class AuthorizationRequest(BaseModel):
    # `account_id` must come from the validated `sub` claim, never the body (design
    # doc: Security → Authorization) — rejecting unknown fields is what turns a
    # client-supplied `account_id` into a 400 instead of a silently ignored no-op.
    model_config = ConfigDict(extra="forbid")

    amount: int = Field(gt=0)
    merchant_id: str


class AuthorizationResponse(BaseModel):
    authorization_id: str
    status: str
    amount: int
    merchant_id: str
    expires_at: str
    created_at: str
    updated_at: str
