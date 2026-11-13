from datetime import datetime, timezone
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.core.database import get_db
from app.core.security import encrypt_credentials
from app.models.entities import Platform, ConnectedAccount
from app.schemas.api import ConnectAccountRequest, ConnectedAccountResponse

router = APIRouter(prefix="", tags=["Platforms & Accounts"])


@router.get("/platforms")
async def list_platforms(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Platform))
    platforms = res.scalars().all()
    return [
        {
            "id": p.id,
            "code": p.code,
            "display_name": p.display_name,
            "adapter_class": p.adapter_class,
            "config_schema": p.config_schema,
        }
        for p in platforms
    ]


@router.post("/platforms/{platform_id}/accounts/connect", response_model=ConnectedAccountResponse)
async def connect_account(
    platform_id: int,
    req: ConnectAccountRequest,
    db: AsyncSession = Depends(get_db)
):
    plat_res = await db.execute(select(Platform).where(Platform.id == platform_id))
    platform = plat_res.scalar_one_or_none()
    if not platform:
        raise HTTPException(status_code=404, detail="Platform not found")

    encrypted_creds = encrypt_credentials(req.credentials)

    # Check if account already exists
    stmt = select(ConnectedAccount).where(
        ConnectedAccount.platform_id == platform_id,
        ConnectedAccount.external_account_id == req.external_account_id,
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing:
        existing.credentials_encrypted = encrypted_creds
        existing.status = "active"
        existing.connected_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(existing)
        account = existing
    else:
        account = ConnectedAccount(
            platform_id=platform_id,
            external_account_id=req.external_account_id,
            credentials_encrypted=encrypted_creds,
            status="active",
            connected_at=datetime.now(timezone.utc),
        )
        db.add(account)
        await db.commit()
        await db.refresh(account)

    return ConnectedAccountResponse(
        id=account.id,
        platform_id=account.platform_id,
        platform_code=platform.code,
        external_account_id=account.external_account_id,
        status=account.status,
        connected_at=account.connected_at,
    )


@router.get("/accounts", response_model=List[ConnectedAccountResponse])
async def list_connected_accounts(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(ConnectedAccount).options(selectinload(ConnectedAccount.platform)))
    accounts = res.scalars().all()
    return [
        ConnectedAccountResponse(
            id=a.id,
            platform_id=a.platform_id,
            platform_code=a.platform.code if a.platform else "unknown",
            external_account_id=a.external_account_id,
            status=a.status,
            connected_at=a.connected_at,
        )
        for a in accounts
    ]
