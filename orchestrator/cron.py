"""
Cron job handlers for maintenance and stats collection.
"""

import asyncio
import os
import sys
from datetime import datetime

import httpx

from shared.logging import get_logger

logger = get_logger(__name__)


async def run_maintenance():
    """Run nightly maintenance tasks."""
    endpoint = os.getenv("MAINTENANCE_ENDPOINT", "http://localhost:8000/internal/maintenance")
    internal_key = os.getenv("INTERNAL_KEY")
    
    if not internal_key:
        logger.error("INTERNAL_KEY not set")
        sys.exit(1)
        
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                endpoint,
                headers={"X-Internal-Key": internal_key},
                json={"timestamp": datetime.utcnow().isoformat()},
                timeout=300,  # 5 minute timeout
            )
            
            if response.status_code == 200:
                logger.info(f"Maintenance completed: {response.json()}")
            else:
                logger.error(f"Maintenance failed: {response.status_code}")
                sys.exit(1)
                
        except Exception as e:
            logger.error(f"Maintenance error: {e}")
            sys.exit(1)


async def run_stats():
    """Collect hourly statistics."""
    endpoint = os.getenv("STATS_ENDPOINT", "http://localhost:8000/internal/stats/snapshot")
    internal_key = os.getenv("INTERNAL_KEY")
    
    if not internal_key:
        logger.error("INTERNAL_KEY not set")
        sys.exit(1)
        
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                endpoint,
                headers={"X-Internal-Key": internal_key},
                json={"timestamp": datetime.utcnow().isoformat()},
                timeout=60,
            )
            
            if response.status_code == 200:
                logger.info(f"Stats collected: {response.json()}")
            else:
                logger.error(f"Stats collection failed: {response.status_code}")
                sys.exit(1)
                
        except Exception as e:
            logger.error(f"Stats error: {e}")
            sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m orchestrator.cron [maintenance|stats]")
        sys.exit(1)
        
    command = sys.argv[1]
    
    if command == "maintenance":
        asyncio.run(run_maintenance())
    elif command == "stats":
        asyncio.run(run_stats())
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)