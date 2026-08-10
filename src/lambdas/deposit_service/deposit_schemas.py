from pydantic import BaseModel, ConfigDict, Field


class DepositRequest(BaseModel):
    """Request schema for creating a deposit."""

    model_config = ConfigDict(extra="forbid")

    amount: int = Field(gt=0)


class DepositResponse(BaseModel):
    """Response schema for creating a deposit."""

    current_balance: int
    available_balance: int
