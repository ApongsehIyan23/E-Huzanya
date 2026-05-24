"""
E-Huzanya Gateway — Authentication
====================================

MVP: Static API key authentication.
Production: OAuth 2.0 Client Credentials flow.
"""

from fastapi import Header, HTTPException, status
from app.core.config import API_KEYS


async def verify_api_key(
    x_api_key: str = Header(..., alias="X-API-Key"),
    x_ehz_source_agency: str = Header(None, alias="X-EHZ-Source-Agency"),
) -> dict:
    """Validate the API key and return the associated agency info.
    
    Returns:
        dict with 'agency' and 'scopes' keys
    """
    key_info = API_KEYS.get(x_api_key)
    if not key_info:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )
    
    # If source agency header is provided, verify it matches the key
    # (SIMULATOR key can act as any agency)
    if x_ehz_source_agency and key_info["agency"] != "SIMULATOR":
        if key_info["agency"] != x_ehz_source_agency:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"API key belongs to {key_info['agency']}, not {x_ehz_source_agency}",
            )
    
    return key_info


def require_scope(required_scope: str):
    """Factory for scope-checking dependencies."""
    async def check_scope(
        auth: dict = None,
        x_api_key: str = Header(..., alias="X-API-Key"),
    ) -> dict:
        key_info = API_KEYS.get(x_api_key)
        if not key_info:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key",
            )
        if required_scope not in key_info["scopes"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required scope: {required_scope}",
            )
        return key_info
    return check_scope
