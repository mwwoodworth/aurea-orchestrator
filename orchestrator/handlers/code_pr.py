"""
Handler for code PR tasks using Claude Code.
"""
import asyncio
import os
import re
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from shared import AIClientManager, CodePRPayload, GitHubClient, get_task_logger

logger = get_task_logger(__name__)


async def handle_code_pr(task_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle code PR task using Claude Code.
    
    Workflow:
    1. Parse repository info
    2. Create feature branch
    3. Generate implementation plan
    4. Edit files based on goals
    5. Run tests if specified
    6. Commit changes
    7. Open pull request
    """
    logger.info(f"Starting code PR task {task_id}", extra_data={"payload": payload})
    
    try:
        # Parse and validate payload
        pr_payload = CodePRPayload(**payload)
        
        # Parse repository URL
        parsed_url = urlparse(pr_payload.repo_url)
        path_parts = parsed_url.path.strip("/").split("/")
        if len(path_parts) < 2:
            raise ValueError(f"Invalid repository URL: {pr_payload.repo_url}")
        
        owner = path_parts[0]
        repo = path_parts[1].replace(".git", "")
        
        # Initialize clients
        github_client = GitHubClient(
            app_id=os.getenv("GITHUB_APP_ID", ""),
            installation_id=os.getenv("GITHUB_INSTALLATION_ID", ""),
            private_key_base64=os.getenv("GITHUB_PRIVATE_KEY_BASE64", "")
        )
        await github_client.connect()
        
        ai_manager = AIClientManager()
        await ai_manager.connect()
        
        # Create feature branch
        branch_name = f"aurea/{task_id[:8]}"
        success = await github_client.create_branch(
            owner=owner,
            repo=repo,
            branch_name=branch_name,
            base_branch=pr_payload.base_branch
        )
        
        if not success:
            raise Exception("Failed to create feature branch")
        
        logger.info(f"Created branch {branch_name}")
        
        # Generate implementation plan using Claude
        plan_prompt = f"""
        Repository: {pr_payload.repo_url}
        Goals: {', '.join(pr_payload.goals)}
        Constraints: {', '.join(pr_payload.constraints)}
        Files to modify: {', '.join(pr_payload.files_to_modify) if pr_payload.files_to_modify else 'Auto-detect'}
        
        Generate a detailed implementation plan with specific file changes needed.
        Format as a list of actions with file paths and changes.
        """
        
        plan = await ai_manager.complete_with_claude(
            messages=[
                {"role": "user", "content": plan_prompt}
            ],
            temperature=0.3,
            max_tokens=2000
        )
        
        if not plan:
            raise Exception("Failed to generate implementation plan")
        
        logger.info("Generated implementation plan", extra_data={"plan": plan[:500]})
        
        # Extract file operations from plan
        file_operations = extract_file_operations(plan)
        
        # Process each file operation
        changes_made = []
        for operation in file_operations:
            file_path = operation["path"]
            action = operation["action"]
            
            if action == "create":
                # Generate new file content
                content = await generate_file_content(
                    ai_manager,
                    file_path,
                    operation.get("description", ""),
                    pr_payload.goals
                )
                
                success = await github_client.create_or_update_file(
                    owner=owner,
                    repo=repo,
                    path=file_path,
                    content=content,
                    message=f"Add {file_path}",
                    branch=branch_name
                )
                
                if success:
                    changes_made.append(f"Created {file_path}")
                    
            elif action == "modify":
                # Get existing content
                existing_content = await github_client.get_file_content(
                    owner=owner,
                    repo=repo,
                    path=file_path,
                    ref=branch_name
                )
                
                if existing_content:
                    # Generate modifications
                    modified_content = await modify_file_content(
                        ai_manager,
                        file_path,
                        existing_content,
                        operation.get("changes", ""),
                        pr_payload.goals
                    )
                    
                    success = await github_client.create_or_update_file(
                        owner=owner,
                        repo=repo,
                        path=file_path,
                        content=modified_content,
                        message=f"Update {file_path}",
                        branch=branch_name
                    )
                    
                    if success:
                        changes_made.append(f"Modified {file_path}")
        
        logger.info(f"Made {len(changes_made)} changes", extra_data={"changes": changes_made})
        
        # Run tests if specified
        test_results = None
        if pr_payload.test_command:
            logger.info(f"Running tests: {pr_payload.test_command}")
            # Note: This would require a CI/CD integration or remote execution capability
            # For now, we'll add a comment about tests needing to be run
            test_results = "Tests should be run with: " + pr_payload.test_command
        
        # Create pull request
        pr_body = generate_pr_body(
            goals=pr_payload.goals,
            changes=changes_made,
            plan=plan,
            test_results=test_results
        )
        
        pr_data = await github_client.create_pull_request(
            owner=owner,
            repo=repo,
            title=pr_payload.pr_title,
            body=pr_body or pr_payload.pr_description or pr_body,
            head_branch=branch_name,
            base_branch=pr_payload.base_branch
        )
        
        if not pr_data:
            raise Exception("Failed to create pull request")
        
        logger.info(f"Created PR #{pr_data['number']}: {pr_data['html_url']}")
        
        # Disconnect clients
        await github_client.disconnect()
        await ai_manager.disconnect()
        
        return {
            "status": "success",
            "pr_url": pr_data["html_url"],
            "pr_number": pr_data["number"],
            "branch": branch_name,
            "changes": changes_made,
            "plan": plan[:1000]  # Truncate for storage
        }
        
    except Exception as e:
        logger.exception(f"Error in code PR handler: {e}")
        return {
            "status": "failed",
            "error": str(e)
        }


def extract_file_operations(plan: str) -> list:
    """Extract file operations from implementation plan."""
    operations = []
    
    # Look for file paths and actions in the plan
    lines = plan.split("\n")
    for line in lines:
        # Match patterns like "Create/Modify/Update file.py"
        if match := re.search(r"(create|modify|update|add|edit)\s+(\S+\.\w+)", line, re.I):
            action = "create" if match.group(1).lower() in ["create", "add"] else "modify"
            path = match.group(2)
            operations.append({
                "action": action,
                "path": path,
                "description": line
            })
    
    return operations


async def generate_file_content(
    ai_manager: AIClientManager,
    file_path: str,
    description: str,
    goals: list
) -> str:
    """Generate content for a new file."""
    prompt = f"""
    Generate content for file: {file_path}
    Description: {description}
    Goals: {', '.join(goals)}
    
    Provide production-ready code with proper error handling and documentation.
    """
    
    content = await ai_manager.complete_with_claude(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=4000
    )
    
    return content or ""


async def modify_file_content(
    ai_manager: AIClientManager,
    file_path: str,
    existing_content: str,
    changes: str,
    goals: list
) -> str:
    """Modify existing file content."""
    prompt = f"""
    Modify file: {file_path}
    Changes needed: {changes}
    Goals: {', '.join(goals)}
    
    Current content:
    ```
    {existing_content[:3000]}
    ```
    
    Provide the complete modified file content.
    """
    
    content = await ai_manager.complete_with_claude(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=4000
    )
    
    return content or existing_content


def generate_pr_body(
    goals: list,
    changes: list,
    plan: str,
    test_results: Optional[str] = None
) -> str:
    """Generate pull request body."""
    body = "## 🤖 AUREA Automated PR\n\n"
    
    body += "### Goals\n"
    for goal in goals:
        body += f"- {goal}\n"
    
    body += "\n### Changes Made\n"
    for change in changes:
        body += f"- {change}\n"
    
    if test_results:
        body += f"\n### Test Results\n{test_results}\n"
    
    body += "\n### Implementation Plan\n"
    body += "```\n"
    body += plan[:1000]
    if len(plan) > 1000:
        body += "\n... (truncated)"
    body += "\n```\n"
    
    body += "\n---\n"
    body += "Generated by AUREA Orchestrator\n"
    
    return body