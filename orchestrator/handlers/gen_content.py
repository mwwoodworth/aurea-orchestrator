"""
Handler for content generation tasks.
"""
import os
from typing import Any, Dict

from shared import AIClientManager, ContentGenerationPayload, SupabaseClient, get_task_logger

logger = get_task_logger(__name__)


async def handle_gen_content(task_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Handle content generation task."""
    logger.info(f"Starting content generation task {task_id}", extra_data={"payload": payload})
    
    try:
        gen_payload = ContentGenerationPayload(**payload)
        
        # Initialize AI client
        ai_manager = AIClientManager()
        await ai_manager.connect()
        
        # Generate content based on model
        content = None
        if "claude" in gen_payload.model:
            content = await ai_manager.complete_with_claude(
                messages=[{"role": "user", "content": gen_payload.prompt}],
                model=gen_payload.model,
                max_tokens=gen_payload.max_tokens,
                temperature=gen_payload.temperature
            )
        elif "gpt" in gen_payload.model:
            content = await ai_manager.complete_with_gpt(
                messages=[{"role": "user", "content": gen_payload.prompt}],
                model=gen_payload.model,
                max_tokens=gen_payload.max_tokens,
                temperature=gen_payload.temperature
            )
        elif "gemini" in gen_payload.model:
            content = await ai_manager.complete_with_gemini(
                prompt=gen_payload.prompt,
                model=gen_payload.model,
                max_tokens=gen_payload.max_tokens,
                temperature=gen_payload.temperature
            )
        
        await ai_manager.disconnect()
        
        if not content:
            raise Exception("Failed to generate content")
        
        # Save to storage if requested
        storage_url = None
        if gen_payload.save_to_storage and os.getenv("SUPABASE_URL"):
            supabase = SupabaseClient(
                os.getenv("SUPABASE_URL"),
                os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
            )
            await supabase.connect()
            
            # Determine file extension
            ext_map = {"markdown": "md", "html": "html", "json": "json", "text": "txt"}
            extension = ext_map.get(gen_payload.output_format, "txt")
            
            storage_url = await supabase.upload_file(
                bucket="content",
                path=f"generated/{task_id}.{extension}",
                content=content.encode("utf-8"),
                content_type=f"text/{gen_payload.output_format}"
            )
            
            await supabase.disconnect()
        
        logger.info(f"Generated {len(content)} characters of content")
        
        return {
            "status": "success",
            "content": content,
            "model": gen_payload.model,
            "format": gen_payload.output_format,
            "storage_url": storage_url,
            "character_count": len(content)
        }
        
    except Exception as e:
        logger.exception(f"Error in content generation handler: {e}")
        return {
            "status": "failed",
            "error": str(e)
        }