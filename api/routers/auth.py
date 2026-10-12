from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from services.auth_service import (
    BRAND_DEMO_PASSWORD,
    issue_access_token,
    list_brand_accounts,
    verify_brand_login,
)
from suffai.api.envelope import err, ok

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class BrandLoginRequest(BaseModel):
    brand: str = Field(..., description="Brand account name")
    password: str


@router.get("/brands")
def brands():
    return ok({"brands": list_brand_accounts(), "demo_hint": BRAND_DEMO_PASSWORD})


@router.post("/brand/login")
def brand_login(body: BrandLoginRequest):
    user = verify_brand_login(body.brand, body.password)
    if not user:
        return err("UNAUTHORIZED", "Invalid brand credentials")
    token = issue_access_token(user)
    return ok({"access_token": token, "token_type": "bearer", "user": user})
