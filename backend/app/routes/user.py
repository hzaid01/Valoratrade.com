import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Header, Depends
from pydantic import BaseModel
from typing import Optional
from slowapi import Limiter
from slowapi.util import get_remote_address
from app.db import get_db
from app.firebase_config import verify_firebase_token
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
    Validate and extract the Firebase ID token from Authorization header.
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
    Verify Firebase ID token and extract user ID.
    """
    try:
        logger.info(f"Verifying Firebase token (length: {len(token) if token else 0})")
        
        if not token or len(token) < 100:
            logger.warning("Token appears too short or empty")
            raise HTTPException(status_code=401, detail="Invalid token format")
        
        # Verify token using Firebase Admin SDK
        decoded_token = verify_firebase_token(token)
        user_id = decoded_token.get('uid')
        
        if not user_id:
            logger.warning("No 'uid' in decoded token")
            raise HTTPException(status_code=401, detail="Invalid token: no user ID")
        
        logger.info(f"Token validated successfully for user: {user_id}")
        return user_id
            
    except ValueError as e:
        logger.warning(f"Token verification failed: {e}")
        raise HTTPException(status_code=401, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Token verification error: {type(e).__name__}: {e}")
        raise HTTPException(status_code=401, detail="Authentication failed")


@router.get("/settings")
async def get_user_settings(token: str = Depends(validate_authorization)):
    """
    Get user API keys (masked for security).
    """
    try:
        user_id = get_user_id_from_token(token)
        logger.info(f"Fetching settings for user: {user_id}")
        
        db = get_db()
        
        try:
            # Query Firestore for user's API keys
            doc_ref = db.collection("user_api_keys").document(user_id)
            doc = doc_ref.get()
            result_data = doc.to_dict() if doc.exists else None
        except Exception as db_error:
            logger.warning(f"Database error: {db_error}. Returning empty settings.")
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
        
        db = get_db()
        doc_ref = db.collection("user_api_keys").document(user_id)
        
        # Check if user already has keys stored
        logger.info("Checking for existing keys...")
        doc = doc_ref.get()
        existing_data = doc.to_dict() if doc.exists else None
        logger.info(f"Existing data found: {existing_data is not None}")

        # Only encrypt non-empty values, preserve existing encrypted values if new value is masked
        def should_update_key(new_value: str, existing_encrypted: str) -> str:
            """Determine if we should update the key or keep existing."""
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
        
        # Safely get values from keys model
        binance_api = keys.binance_api_key if keys.binance_api_key else ""
        binance_secret = keys.binance_secret_key if keys.binance_secret_key else ""
        openai_key = keys.openai_api_key if keys.openai_api_key else ""
        
        logger.info(f"Input values - binance_api: {len(binance_api)} chars, binance_secret: {len(binance_secret)} chars, openai: {len(openai_key)} chars")
        
        data = {
            "binance_api_key": should_update_key(binance_api, existing_binance_key),
            "binance_secret_key": should_update_key(binance_secret, existing_binance_secret),
            "openai_api_key": should_update_key(openai_key, existing_openai_key),
            "updated_at": datetime.now(timezone.utc)
        }

        if existing_data:
            logger.info("Updating existing record...")
            doc_ref.update(data)
        else:
            logger.info("Creating new record...")
            data["created_at"] = datetime.now(timezone.utc)
            doc_ref.set(data)

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
