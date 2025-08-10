#!/usr/bin/env python3
"""
API key rotation utility for zero-downtime key updates.
"""

import asyncio
import argparse
import os
import sys
from datetime import datetime, timedelta

import asyncpg

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.security import APIKeyManager, UserRole
from shared.logging import get_logger

logger = get_logger(__name__)


async def main():
    parser = argparse.ArgumentParser(description="Rotate API keys safely")
    parser.add_argument("old_key_id", help="ID of key to rotate")
    parser.add_argument(
        "--overlap-minutes",
        type=int,
        default=60,
        help="Overlap period before revoking old key (default: 60)",
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL"),
        help="PostgreSQL connection URL",
    )
    parser.add_argument(
        "--salt",
        default=os.getenv("API_KEY_SALT", "default_salt"),
        help="Salt for key hashing",
    )
    
    args = parser.parse_args()
    
    if not args.database_url:
        print("Error: DATABASE_URL required")
        sys.exit(1)
        
    pool = await asyncpg.create_pool(args.database_url)
    
    try:
        manager = APIKeyManager(pool, args.salt)
        
        # Get old key details
        async with pool.acquire() as conn:
            old_key = await conn.fetchrow(
                "SELECT * FROM orchestrator_api_keys WHERE id = $1",
                args.old_key_id,
            )
            
        if not old_key:
            print(f"Error: Key {args.old_key_id} not found")
            sys.exit(1)
            
        print(f"Rotating key: {old_key['name']} (role: {old_key['role']})")
        
        # Perform rotation
        new_key_id, new_raw_key = await manager.rotate_key(
            args.old_key_id,
            overlap_minutes=args.overlap_minutes,
        )
        
        expiry = datetime.utcnow() + timedelta(minutes=args.overlap_minutes)
        
        print("\n✅ Key rotation successful!")
        print(f"New Key ID: {new_key_id}")
        print(f"New API Key: {new_raw_key}")
        print(f"\n⚠️  IMPORTANT:")
        print(f"1. Update all services to use the new key")
        print(f"2. Old key will expire at: {expiry.isoformat()}")
        print(f"3. Save the new key securely - it cannot be retrieved again!")
        
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())