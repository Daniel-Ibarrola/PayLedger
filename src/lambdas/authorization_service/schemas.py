from pydantic import BaseModel, Field


class AuthorizationRequest(BaseModel):
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
