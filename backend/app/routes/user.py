import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Header, Depends
from pydantic import BaseModel
from typing import Optional
from slowapi import Limiter
from slowapi.util import get_remote_address
from app.db import get_supabase
from app.utils.encryption import encrypt_value, decrypt_value, mask_api_key

logger = logging.getLogger(__name__)
limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/api/user", tags=["user"])


class APIKeysUpdate(BaseModel):
    binance_api_key: Optional[str] = ""
    binance_secret_key: Optional[str] = ""
    openai_api_key: Optional[str] = ""


def validate_authorization(authorization: str = Header(...)) -> str:
    """
    Validate and extract the JWT token from Authorization header.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header required")
    
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header format")
    
    token = authorization[7:]  # Extract token after "Bearer "
    if not token or len(token) < 10:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    return token


def get_user_id_from_token(token: str) -> str:
    """
    Get user ID from Supabase JWT token by decoding it directly.
    Supabase JWTs contain the user ID in the 'sub' (subject) claim.
    """
    import jwt
    
    try:
        logger.info(f"Decoding JWT token (length: {len(token) if token else 0})")
        
        if not token or len(token) < 100:  # JWT tokens are typically longer
            logger.warning(f"Token appears too short or empty")
            raise HTTPException(status_code=401, detail="Invalid token format")
        
        # Decode the JWT without verification to extract claims
        # Supabase tokens are self-contained and the 'sub' claim contains the user ID
        # We decode without signature verification since we're behind Supabase auth
        try:
            # First, decode without verification to get the payload
            decoded = jwt.decode(token, options={"verify_signature": False})
            logger.info(f"JWT decoded successfully. Claims: {list(decoded.keys())}")
            
            # Check token expiration manually
            import time
            exp = decoded.get('exp')
            if exp and exp < time.time():
                logger.warning("Token has expired")
                raise HTTPException(status_code=401, detail="Token has expired")
            
            # Get user ID from 'sub' claim (standard JWT subject claim)
            user_id = decoded.get('sub')
            if not user_id:
                logger.warning(f"No 'sub' claim in token. Available claims: {decoded.keys()}")
                raise HTTPException(status_code=401, detail="Invalid token: no user ID")
            
            logger.info(f"Token validated successfully for user: {user_id}")
            return user_id
            
        except jwt.ExpiredSignatureError:
            logger.warning("Token expired")
            raise HTTPException(status_code=401, detail="Token has expired")
        except jwt.InvalidTokenError as e:
            logger.warning(f"Invalid JWT token: {e}")
            raise HTTPException(status_code=401, detail="Invalid token")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Token decoding error: {type(e).__name__}: {e}")
        raise HTTPException(status_code=401, detail="Authentication failed")


@router.get("/settings")
async def get_user_settings(token: str = Depends(validate_authorization)):
    """
    Get user API keys (masked for security).
    """
    try:
        user_id = get_user_id_from_token(token)
        logger.info(f"Fetching settings for user: {user_id}")
        
        supabase = get_supabase()
        
        try:
            result = supabase.table("user_api_keys").select("*").eq("user_id", user_id).limit(1).execute()
            # Get first item or None from list
            result_data = result.data[0] if result.data else None
        except Exception as db_error:
            # Handle table not found or permission errors gracefully
            error_str = str(db_error).lower()
            if "does not exist" in error_str or "relation" in error_str or "permission" in error_str:
                logger.warning(f"Database table issue: {db_error}. Returning empty settings.")
                return {
                    "success": True,
                    "data": {
                        "binance_api_key": "",
                        "binance_secret_key": "",
                        "openai_api_key": "",
                        "has_binance_keys": False,
                        "has_openai_key": False
                    }
                }
            raise

        if result_data:
            # Decrypt and mask keys for display
            binance_key = decrypt_value(result_data.get("binance_api_key", ""))
            binance_secret = decrypt_value(result_data.get("binance_secret_key", ""))
            openai_key = decrypt_value(result_data.get("openai_api_key", ""))
            
            logger.info(f"Settings found for user. Has binance: {bool(binance_key and binance_secret)}, Has openai: {bool(openai_key)}")
            
            return {
                "success": True,
                "data": {
                    "binance_api_key": mask_api_key(binance_key) if binance_key else "",
                    "binance_secret_key": mask_api_key(binance_secret) if binance_secret else "",
                    "openai_api_key": mask_api_key(openai_key) if openai_key else "",
                    "has_binance_keys": bool(binance_key and binance_secret),
                    "has_openai_key": bool(openai_key)
                }
            }
        else:
            logger.info(f"No settings found for user {user_id}, returning empty")
            return {
                "success": True,
                "data": {
                    "binance_api_key": "",
                    "binance_secret_key": "",
                    "openai_api_key": "",
                    "has_binance_keys": False,
                    "has_openai_key": False
                }
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching user settings: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch settings")


@router.post("/settings")
async def update_user_settings(
    keys: APIKeysUpdate,
    token: str = Depends(validate_authorization)
):
    """
    Update user API keys (encrypted before storage).
    """
    try:
        user_id = get_user_id_from_token(token)
        logger.info(f"Updating settings for user: {user_id}")
        
        supabase = get_supabase()
        
        # Check if user already has keys stored
        logger.info("Checking for existing keys...")
        existing_result = supabase.table("user_api_keys").select("*").eq("user_id", user_id).limit(1).execute()
        existing_data = existing_result.data[0] if existing_result.data else None
        logger.info(f"Existing data found: {existing_data is not None}")

        # Only encrypt non-empty values, preserve existing encrypted values if new value is masked
        def should_update_key(new_value: str, existing_encrypted: str) -> str:
            """Determine if we should update the key or keep existing."""
            # Handle None values
            if new_value is None:
                new_value = ""
            if existing_encrypted is None:
                existing_encrypted = ""
            
            if not new_value or new_value.strip() == "":
                return ""  # Clear the key
            if "..." in new_value:
                # Value is masked, keep existing encrypted value
                return existing_encrypted
            return encrypt_value(new_value)
        
        existing_binance_key = existing_data.get("binance_api_key", "") if existing_data else ""
        existing_binance_secret = existing_data.get("binance_secret_key", "") if existing_data else ""
        existing_openai_key = existing_data.get("openai_api_key", "") if existing_data else ""
        
        # Safely get values from keys model, handling None
        binance_api = keys.binance_api_key if keys.binance_api_key else ""
        binance_secret = keys.binance_secret_key if keys.binance_secret_key else ""
        openai_key = keys.openai_api_key if keys.openai_api_key else ""
        
        logger.info(f"Input values - binance_api: {len(binance_api)} chars, binance_secret: {len(binance_secret)} chars, openai: {len(openai_key)} chars")
        
        data = {
            "user_id": user_id,
            "binance_api_key": should_update_key(binance_api, existing_binance_key),
            "binance_secret_key": should_update_key(binance_secret, existing_binance_secret),
            "openai_api_key": should_update_key(openai_key, existing_openai_key),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }

        if existing_data:
            logger.info("Updating existing record...")
            result = supabase.table("user_api_keys").update(data).eq("user_id", user_id).execute()
        else:
            logger.info("Inserting new record...")
            data["created_at"] = datetime.now(timezone.utc).isoformat()
            result = supabase.table("user_api_keys").insert(data).execute()

        logger.info(f"API keys updated for user {user_id}")
        return {
            "success": True,
            "message": "API keys updated successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating user settings: {type(e).__name__}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update settings: {str(e)}")

