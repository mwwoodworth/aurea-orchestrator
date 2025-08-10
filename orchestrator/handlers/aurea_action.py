"""
Handler for composite AUREA action tasks.
"""
import asyncio
from typing import Any, Dict

from shared import AIClientManager, AureaActionPayload, get_task_logger

logger = get_task_logger(__name__)


async def handle_aurea_action(task_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Handle composite AUREA action with multiple steps."""
    logger.info(f"Starting AUREA action task {task_id}", extra_data={"payload": payload})
    
    try:
        action_payload = AureaActionPayload(**payload)
        
        # Initialize AI for orchestration
        ai_manager = AIClientManager()
        await ai_manager.connect()
        
        results = []
        context = action_payload.context.copy()
        
        # Execute each step in sequence
        for i, step in enumerate(action_payload.steps):
            step_name = step.get("name", f"step_{i+1}")
            step_type = step.get("type", "ai_task")
            
            logger.info(f"Executing step {step_name} ({step_type})")
            
            if step_type == "ai_task":
                # Execute AI task
                prompt = step.get("prompt", "")
                # Interpolate context variables
                for key, value in context.items():
                    prompt = prompt.replace(f"{{{key}}}", str(value))
                
                result = await ai_manager.complete_with_claude(
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=step.get("max_tokens", 2000),
                    temperature=step.get("temperature", 0.7)
                )
                
                results.append({
                    "step": step_name,
                    "type": step_type,
                    "result": result
                })
                
                # Update context with result
                context[f"step_{i+1}_result"] = result
                
            elif step_type == "tool_call":
                # Execute tool call (placeholder for MCP tools)
                tool_name = step.get("tool", "")
                tool_params = step.get("params", {})
                
                # Interpolate context in params
                for key, value in tool_params.items():
                    if isinstance(value, str):
                        for ctx_key, ctx_value in context.items():
                            value = value.replace(f"{{{ctx_key}}}", str(ctx_value))
                        tool_params[key] = value
                
                # Simulate tool execution
                result = f"Executed {tool_name} with params: {tool_params}"
                
                results.append({
                    "step": step_name,
                    "type": step_type,
                    "tool": tool_name,
                    "result": result
                })
                
                context[f"step_{i+1}_result"] = result
                
            elif step_type == "conditional":
                # Evaluate condition
                condition = step.get("condition", "")
                for key, value in context.items():
                    condition = condition.replace(f"{{{key}}}", str(value))
                
                # Simple evaluation (in production, use safe eval)
                if eval(condition):
                    # Execute then branch
                    then_steps = step.get("then", [])
                    for then_step in then_steps:
                        action_payload.steps.append(then_step)
                else:
                    # Execute else branch
                    else_steps = step.get("else", [])
                    for else_step in else_steps:
                        action_payload.steps.append(else_step)
                
                results.append({
                    "step": step_name,
                    "type": step_type,
                    "condition": condition,
                    "evaluated": eval(condition)
                })
            
            # Check timeout
            if i > 0 and i % 5 == 0:
                await asyncio.sleep(0.1)  # Yield to event loop
        
        await ai_manager.disconnect()
        
        logger.info(f"Completed {len(results)} steps in AUREA action")
        
        return {
            "status": "success",
            "workflow": action_payload.workflow,
            "steps_executed": len(results),
            "results": results,
            "final_context": context
        }
        
    except Exception as e:
        logger.exception(f"Error in AUREA action handler: {e}")
        return {
            "status": "failed",
            "error": str(e)
        }