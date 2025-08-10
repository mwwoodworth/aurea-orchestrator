"""
Security module for HMAC webhook verification and API key RBAC.
"""

import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple
from enum import Enum

from fastapi import HTTPException, Request, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import asyncpg

from shared.logging import get_logger

logger = get_logger(__name__)


class UserRole(str, Enum):
    """User roles for RBAC."""
    ADMIN = "admin"
    SERVICE = "service"
    READONLY = "readonly"


class SecurityConfig:
    """Security configuration."""
    WEBHOOK_SECRET: str = ""
    API_KEY_SALT: str = ""
    TIMESTAMP_TOLERANCE_SECONDS: int = 300  # 5 minutes
    REPLAY_WINDOW_SECONDS: int = 86400  # 24 hours


class WebhookVerifier:
    """Verify webhook signatures and prevent replay attacks."""
    
    def __init__(self, secret: str, db_pool: Optional[asyncpg.Pool] = None):
        self.secret = secret.encode() if isinstance(secret, str) else secret
        self.db_pool = db_pool
        
    def compute_signature(
        self,
        payload: bytes,
        timestamp: Optional[str] = None,
    ) -> str:
        """Compute HMAC-SHA256 signature."""
        if timestamp:
            # Include timestamp in signature to prevent replay
            message = f"{timestamp}.{payload.decode()}".encode()
        else:
            message = payload
            
        signature = hmac.new(
            self.secret,
            message,
            hashlib.sha256
        ).hexdigest()
        
        return f"sha256={signature}"
        
    async def verify_github(
        self,
        payload: bytes,
        signature_header: str,
        event_id: Optional[str] = None,
    ) -> bool:
        """Verify GitHub webhook signature."""
        if not signature_header:
            logger.warning("Missing GitHub signature header")
            return False
            
        expected = self.compute_signature(payload)
        
        # Constant-time comparison
        if not hmac.compare_digest(expected, signature_header):
            logger.warning("Invalid GitHub signature")
            return False
            
        # Check for replay if database available
        if self.db_pool and event_id:
            return await self._check_replay("github", event_id)
            
        return True
        
    async def verify_clickup(
        self,
        payload: bytes,
        signature_header: str,
        webhook_id: Optional[str] = None,
    ) -> bool:
        """Verify ClickUp webhook signature."""
        if not signature_header:
            logger.warning("Missing ClickUp signature header")
            return False
            
        # ClickUp uses plain HMAC hex digest
        expected = hmac.new(
            self.secret,
            payload,
            hashlib.sha256
        ).hexdigest()
        
        if not hmac.compare_digest(expected, signature_header):
            logger.warning("Invalid ClickUp signature")
            return False
            
        # Check for replay
        if self.db_pool and webhook_id:
            return await self._check_replay("clickup", webhook_id)
            
        return True
        
    async def verify_generic(
        self,
        payload: bytes,
        signature_header: str,
        timestamp_header: Optional[str] = None,
        source: str = "generic",
        event_id: Optional[str] = None,
    ) -> bool:
        """
        Generic webhook verification with timestamp.
        Expected signature format: sha256=<signature>
        """
        if not signature_header:
            logger.warning(f"Missing {source} signature header")
            return False
            
        # Check timestamp if provided
        if timestamp_header:
            try:
                timestamp = int(timestamp_header)
                current_time = int(time.time())
                
                # Check if timestamp is within tolerance
                if abs(current_time - timestamp) > SecurityConfig.TIMESTAMP_TOLERANCE_SECONDS:
                    logger.warning(f"Webhook timestamp outside tolerance: {abs(current_time - timestamp)}s")
                    return False
                    
            except (ValueError, TypeError):
                logger.warning(f"Invalid timestamp header: {timestamp_header}")
                return False
                
        # Compute and verify signature
        expected = self.compute_signature(payload, timestamp_header)
        
        if not hmac.compare_digest(expected, signature_header):
            logger.warning(f"Invalid {source} signature")
            return False
            
        # Check for replay
        if self.db_pool and event_id:
            return await self._check_replay(source, event_id, signature_header)
            
        return True
        
    async def _check_replay(
        self,
        source: str,
        external_id: str,
        signature: Optional[str] = None,
    ) -> bool:
        """Check if this is a replay attack."""
        if not self.db_pool:
            return True  # Can't check without database
            
        async with self.db_pool.acquire() as conn:
            # Check if already processed
            result = await conn.fetchone(
                """
                SELECT id FROM orchestrator_inbox
                WHERE source = $1 AND external_id = $2
                """,
                source,
                external_id,
            )
            
            if result:
                logger.warning(f"Replay attack detected: {source}/{external_id}")
                return False
                
            # Record the webhook
            signature_hash = hashlib.sha256(
                signature.encode() if signature else b""
            ).hexdigest() if signature else None
            
            await conn.execute(
                """
                INSERT INTO orchestrator_inbox 
                (source, external_id, signature_hash, payload, status)
                VALUES ($1, $2, $3, $4, 'received')
                """,
                source,
                external_id,
                signature_hash,
                {},  # Payload will be updated later
            )
            
        return True


class APIKeyAuth(HTTPBearer):
    """API key authentication with RBAC."""
    
    def __init__(self, db_pool: asyncpg.Pool, required_role: Optional[UserRole] = None):
        super().__init__()
        self.db_pool = db_pool
        self.required_role = required_role
        
    async def __call__(self, request: Request) -> Dict[str, Any]:
        """Verify API key and check role."""
        credentials: HTTPAuthorizationCredentials = await super().__call__(request)
        
        if not credentials:
            raise HTTPException(status_code=401, detail="Missing API key")
            
        # Hash the provided key
        key_hash = hashlib.sha256(
            credentials.credentials.encode()
        ).hexdigest()
        
        async with self.db_pool.acquire() as conn:
            # Look up key
            result = await conn.fetchrow(
                """
                SELECT id, name, role, expires_at, is_active
                FROM orchestrator_api_keys
                WHERE key_hash = $1
                """,
                key_hash,
            )
            
            if not result:
                logger.warning(f"Invalid API key attempted: {key_hash[:8]}...")
                raise HTTPException(status_code=401, detail="Invalid API key")
                
            # Check if active
            if not result["is_active"]:
                raise HTTPException(status_code=401, detail="API key inactive")
                
            # Check expiration
            if result["expires_at"] and result["expires_at"] < datetime.utcnow():
                raise HTTPException(status_code=401, detail="API key expired")
                
            # Check role if required
            if self.required_role:
                user_role = UserRole(result["role"])
                if not self._check_role_permission(user_role, self.required_role):
                    raise HTTPException(
                        status_code=403,
                        detail=f"Insufficient permissions. Required: {self.required_role}"
                    )
                    
            # Update last used
            await conn.execute(
                """
                UPDATE orchestrator_api_keys
                SET last_used_at = NOW()
                WHERE id = $1
                """,
                result["id"],
            )
            
        return {
            "key_id": str(result["id"]),
            "key_name": result["name"],
            "role": result["role"],
        }
        
    def _check_role_permission(self, user_role: UserRole, required_role: UserRole) -> bool:
        """Check if user role has required permission."""
        role_hierarchy = {
            UserRole.READONLY: 0,
            UserRole.SERVICE: 1,
            UserRole.ADMIN: 2,
        }
        
        return role_hierarchy.get(user_role, 0) >= role_hierarchy.get(required_role, 0)


class APIKeyManager:
    """Manage API keys."""
    
    def __init__(self, db_pool: asyncpg.Pool, salt: str):
        self.db_pool = db_pool
        self.salt = salt
        
    async def create_key(
        self,
        name: str,
        role: UserRole,
        description: Optional[str] = None,
        expires_in_days: Optional[int] = None,
        created_by: Optional[str] = None,
    ) -> Tuple[str, str]:
        """
        Create a new API key.
        Returns (key_id, raw_key). The raw key is only returned once!
        """
        # Generate secure random key
        raw_key = secrets.token_urlsafe(32)
        
        # Hash the key
        key_hash = hashlib.sha256(
            f"{raw_key}{self.salt}".encode()
        ).hexdigest()
        
        # Calculate expiration
        expires_at = None
        if expires_in_days:
            expires_at = datetime.utcnow() + timedelta(days=expires_in_days)
            
        async with self.db_pool.acquire() as conn:
            result = await conn.fetchrow(
                """
                INSERT INTO orchestrator_api_keys
                (key_hash, name, role, description, expires_at, created_by)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id
                """,
                key_hash,
                name,
                role.value,
                description,
                expires_at,
                created_by,
            )
            
        logger.info(f"Created API key '{name}' with role {role.value}")
        return str(result["id"]), raw_key
        
    async def revoke_key(self, key_id: str) -> bool:
        """Revoke an API key."""
        async with self.db_pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE orchestrator_api_keys
                SET is_active = false
                WHERE id = $1
                """,
                key_id,
            )
            
        success = result.split()[-1] == "1"
        if success:
            logger.info(f"Revoked API key {key_id}")
        return success
        
    async def rotate_key(
        self,
        old_key_id: str,
        overlap_minutes: int = 60,
    ) -> Tuple[str, str]:
        """
        Rotate an API key with overlap period.
        Returns (new_key_id, new_raw_key).
        """
        async with self.db_pool.acquire() as conn:
            # Get old key details
            old_key = await conn.fetchrow(
                """
                SELECT name, role, description, created_by
                FROM orchestrator_api_keys
                WHERE id = $1
                """,
                old_key_id,
            )
            
            if not old_key:
                raise ValueError(f"Key {old_key_id} not found")
                
        # Create new key
        new_key_id, new_raw_key = await self.create_key(
            name=f"{old_key['name']}_rotated",
            role=UserRole(old_key["role"]),
            description=f"Rotated from {old_key_id}",
            created_by=old_key["created_by"],
        )
        
        # Schedule old key revocation
        revoke_at = datetime.utcnow() + timedelta(minutes=overlap_minutes)
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE orchestrator_api_keys
                SET expires_at = $2
                WHERE id = $1
                """,
                old_key_id,
                revoke_at,
            )
            
        logger.info(f"Rotated key {old_key_id} -> {new_key_id}, old expires at {revoke_at}")
        return new_key_id, new_raw_key
        
    async def list_keys(self, include_inactive: bool = False) -> list:
        """List all API keys."""
        async with self.db_pool.acquire() as conn:
            query = """
                SELECT id, name, role, description, created_at, 
                       expires_at, last_used_at, is_active, created_by
                FROM orchestrator_api_keys
            """
            if not include_inactive:
                query += " WHERE is_active = true"
            query += " ORDER BY created_at DESC"
            
            results = await conn.fetch(query)
            
        return [dict(r) for r in results]