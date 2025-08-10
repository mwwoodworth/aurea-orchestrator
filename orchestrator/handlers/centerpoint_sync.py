"""
Handler for CenterPoint synchronization tasks.
"""
import asyncio
import json
import os
import subprocess
from datetime import datetime
from typing import Any, Dict

from shared import CenterPointSyncPayload, SupabaseClient, get_task_logger

logger = get_task_logger(__name__)


async def handle_centerpoint_sync(task_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle CenterPoint synchronization task.
    
    Executes TypeScript sync scripts with proper environment and captures results.
    """
    logger.info(f"Starting CenterPoint sync task {task_id}", extra_data={"payload": payload})
    
    try:
        # Parse and validate payload
        sync_payload = CenterPointSyncPayload(**payload)
        
        # Determine which script to run
        script_map = {
            "full": "scripts/centerpoint_full_discovery.ts",
            "incremental": "scripts/centerpoint_incremental_sync.ts",
            "status": "scripts/sync_status.ts"
        }
        
        script_path = script_map.get(sync_payload.sync_type)
        if not script_path:
            raise ValueError(f"Invalid sync type: {sync_payload.sync_type}")
        
        # Build environment variables
        env = os.environ.copy()
        env.update({
            "DATABASE_URL": os.getenv("DATABASE_URL", ""),
            "CENTERPOINT_BASE_URL": os.getenv("CENTERPOINT_BASE_URL", "https://api.centerpointconnect.io"),
            "CENTERPOINT_BEARER_TOKEN": os.getenv("CENTERPOINT_BEARER_TOKEN", ""),
            "CENTERPOINT_TENANT_ID": os.getenv("CENTERPOINT_TENANT_ID", ""),
            "NODE_ENV": "production"
        })
        
        # Build command
        base_dir = "/home/mwwoodworth/code/weathercraft-erp"  # CenterPoint sync location
        cmd = ["npx", "tsx", script_path]
        
        # Add entity filters if specified
        if sync_payload.entities:
            cmd.extend(["--entities", ",".join(sync_payload.entities)])
        
        # Add timestamp filter for incremental sync
        if sync_payload.since_timestamp:
            cmd.extend(["--since", sync_payload.since_timestamp.isoformat()])
        
        # Add force flag
        if sync_payload.force:
            cmd.append("--force")
        
        logger.info(f"Executing: {' '.join(cmd)}")
        
        # Execute sync script
        start_time = datetime.utcnow()
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=base_dir,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=600  # 10 minute timeout
        )
        
        end_time = datetime.utcnow()
        duration = (end_time - start_time).total_seconds()
        
        # Parse output
        output = stdout.decode("utf-8") if stdout else ""
        errors = stderr.decode("utf-8") if stderr else ""
        
        # Extract metrics from output
        metrics = extract_sync_metrics(output)
        
        # Store results in Supabase if configured
        if supabase_url := os.getenv("SUPABASE_URL"):
            supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
            if supabase_key:
                supabase = SupabaseClient(supabase_url, supabase_key)
                await supabase.connect()
                
                # Store sync log
                log_url = await supabase.store_logs(
                    task_id=task_id,
                    logs=f"STDOUT:\n{output}\n\nSTDERR:\n{errors}"
                )
                
                await supabase.disconnect()
                
                metrics["log_url"] = log_url
        
        # Check exit code
        if process.returncode != 0:
            logger.error(f"Sync failed with exit code {process.returncode}")
            return {
                "status": "failed",
                "exit_code": process.returncode,
                "error": errors or f"Process exited with code {process.returncode}",
                "output": output[-1000:],  # Last 1000 chars
                "duration_seconds": duration,
                "metrics": metrics
            }
        
        logger.info(f"Sync completed successfully in {duration:.2f}s", extra_data={"metrics": metrics})
        
        return {
            "status": "success",
            "sync_type": sync_payload.sync_type,
            "duration_seconds": duration,
            "metrics": metrics,
            "output_summary": output[-500:] if output else "No output"
        }
        
    except asyncio.TimeoutError:
        logger.error("CenterPoint sync timed out")
        return {
            "status": "failed",
            "error": "Sync operation timed out after 10 minutes"
        }
        
    except Exception as e:
        logger.exception(f"Error in CenterPoint sync handler: {e}")
        return {
            "status": "failed",
            "error": str(e)
        }


def extract_sync_metrics(output: str) -> Dict[str, Any]:
    """Extract metrics from sync output."""
    metrics = {
        "records_synced": 0,
        "errors": 0,
        "warnings": 0
    }
    
    lines = output.split("\n")
    for line in lines:
        # Look for common metric patterns
        if "synced" in line.lower():
            # Extract number
            import re
            if match := re.search(r"(\d+)\s+(?:records?|entities?|items?)", line, re.I):
                metrics["records_synced"] += int(match.group(1))
        
        if "error" in line.lower():
            metrics["errors"] += 1
        
        if "warning" in line.lower():
            metrics["warnings"] += 1
        
        # Look for JSON metrics
        if line.strip().startswith("{") and line.strip().endswith("}"):
            try:
                json_data = json.loads(line)
                if "metrics" in json_data:
                    metrics.update(json_data["metrics"])
            except json.JSONDecodeError:
                pass
    
    return metrics