"""
GitHub client for repository operations.
"""
import base64
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx
import jwt
from httpx import AsyncClient

from shared.logging import get_logger

logger = get_logger(__name__)


class GitHubClient:
    """GitHub App client for repository operations."""

    def __init__(
        self,
        app_id: str,
        installation_id: str,
        private_key_base64: str
    ):
        self.app_id = app_id
        self.installation_id = installation_id
        self.private_key = base64.b64decode(private_key_base64).decode("utf-8")
        self.client: Optional[AsyncClient] = None
        self.token: Optional[str] = None
        self.token_expires_at: Optional[datetime] = None

    async def connect(self) -> None:
        """Initialize HTTP client."""
        self.client = httpx.AsyncClient(
            base_url="https://api.github.com",
            headers={
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "AUREA-Orchestrator/1.0"
            },
            timeout=30.0
        )
        await self._refresh_token()
        logger.info("Connected to GitHub")

    async def disconnect(self) -> None:
        """Close HTTP client."""
        if self.client:
            await self.client.aclose()
            logger.info("Disconnected from GitHub")

    def _generate_jwt(self) -> str:
        """Generate JWT for GitHub App authentication."""
        now = datetime.utcnow()
        payload = {
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=10)).timestamp()),
            "iss": self.app_id
        }
        return jwt.encode(payload, self.private_key, algorithm="RS256")

    async def _refresh_token(self) -> None:
        """Refresh installation access token if needed."""
        if self.token and self.token_expires_at and datetime.utcnow() < self.token_expires_at:
            return

        try:
            jwt_token = self._generate_jwt()
            
            # Get installation access token
            response = await self.client.post(
                f"/app/installations/{self.installation_id}/access_tokens",
                headers={"Authorization": f"Bearer {jwt_token}"}
            )
            
            if response.status_code == 201:
                data = response.json()
                self.token = data["token"]
                self.token_expires_at = datetime.fromisoformat(
                    data["expires_at"].replace("Z", "+00:00")
                )
                
                # Update client headers
                self.client.headers["Authorization"] = f"token {self.token}"
                logger.info("Refreshed GitHub access token")
            else:
                logger.error(f"Failed to get access token: {response.text}")
                raise Exception(f"Failed to get GitHub access token: {response.status_code}")
                
        except Exception as e:
            logger.error(f"Error refreshing token: {e}")
            raise

    async def create_branch(
        self,
        owner: str,
        repo: str,
        branch_name: str,
        base_branch: str = "main"
    ) -> bool:
        """Create a new branch."""
        try:
            await self._refresh_token()
            
            # Get base branch SHA
            response = await self.client.get(f"/repos/{owner}/{repo}/git/refs/heads/{base_branch}")
            if response.status_code != 200:
                logger.error(f"Failed to get base branch: {response.text}")
                return False
                
            base_sha = response.json()["object"]["sha"]
            
            # Create new branch
            response = await self.client.post(
                f"/repos/{owner}/{repo}/git/refs",
                json={
                    "ref": f"refs/heads/{branch_name}",
                    "sha": base_sha
                }
            )
            
            if response.status_code == 201:
                logger.info(f"Created branch {branch_name} from {base_branch}")
                return True
            else:
                logger.error(f"Failed to create branch: {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Error creating branch: {e}")
            return False

    async def create_or_update_file(
        self,
        owner: str,
        repo: str,
        path: str,
        content: str,
        message: str,
        branch: str
    ) -> bool:
        """Create or update a file in the repository."""
        try:
            await self._refresh_token()
            
            # Check if file exists
            response = await self.client.get(
                f"/repos/{owner}/{repo}/contents/{path}",
                params={"ref": branch}
            )
            
            sha = None
            if response.status_code == 200:
                sha = response.json()["sha"]
            
            # Create or update file
            data = {
                "message": message,
                "content": base64.b64encode(content.encode()).decode(),
                "branch": branch
            }
            if sha:
                data["sha"] = sha
            
            response = await self.client.put(
                f"/repos/{owner}/{repo}/contents/{path}",
                json=data
            )
            
            if response.status_code in [200, 201]:
                logger.info(f"{'Updated' if sha else 'Created'} file {path}")
                return True
            else:
                logger.error(f"Failed to create/update file: {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Error creating/updating file: {e}")
            return False

    async def create_pull_request(
        self,
        owner: str,
        repo: str,
        title: str,
        body: str,
        head_branch: str,
        base_branch: str = "main"
    ) -> Optional[Dict[str, Any]]:
        """Create a pull request."""
        try:
            await self._refresh_token()
            
            response = await self.client.post(
                f"/repos/{owner}/{repo}/pulls",
                json={
                    "title": title,
                    "body": body,
                    "head": head_branch,
                    "base": base_branch
                }
            )
            
            if response.status_code == 201:
                pr_data = response.json()
                logger.info(f"Created PR #{pr_data['number']}: {title}")
                return pr_data
            else:
                logger.error(f"Failed to create PR: {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Error creating PR: {e}")
            return None

    async def add_comment(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        comment: str
    ) -> bool:
        """Add a comment to an issue or PR."""
        try:
            await self._refresh_token()
            
            response = await self.client.post(
                f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
                json={"body": comment}
            )
            
            if response.status_code == 201:
                logger.info(f"Added comment to issue/PR #{issue_number}")
                return True
            else:
                logger.error(f"Failed to add comment: {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Error adding comment: {e}")
            return False

    async def get_file_content(
        self,
        owner: str,
        repo: str,
        path: str,
        ref: str = "main"
    ) -> Optional[str]:
        """Get file content from repository."""
        try:
            await self._refresh_token()
            
            response = await self.client.get(
                f"/repos/{owner}/{repo}/contents/{path}",
                params={"ref": ref}
            )
            
            if response.status_code == 200:
                data = response.json()
                content = base64.b64decode(data["content"]).decode("utf-8")
                return content
            else:
                logger.error(f"Failed to get file content: {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Error getting file content: {e}")
            return None

    async def list_files(
        self,
        owner: str,
        repo: str,
        path: str = "",
        ref: str = "main"
    ) -> List[Dict[str, Any]]:
        """List files in a directory."""
        try:
            await self._refresh_token()
            
            response = await self.client.get(
                f"/repos/{owner}/{repo}/contents/{path}",
                params={"ref": ref}
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Failed to list files: {response.text}")
                return []
                
        except Exception as e:
            logger.error(f"Error listing files: {e}")
            return []

    async def get_pr_status(
        self,
        owner: str,
        repo: str,
        pr_number: int
    ) -> Optional[Dict[str, Any]]:
        """Get PR status and checks."""
        try:
            await self._refresh_token()
            
            # Get PR details
            response = await self.client.get(f"/repos/{owner}/{repo}/pulls/{pr_number}")
            
            if response.status_code == 200:
                pr_data = response.json()
                
                # Get checks status
                checks_response = await self.client.get(
                    f"/repos/{owner}/{repo}/commits/{pr_data['head']['sha']}/check-runs"
                )
                
                checks = []
                if checks_response.status_code == 200:
                    checks = checks_response.json()["check_runs"]
                
                return {
                    "state": pr_data["state"],
                    "merged": pr_data["merged"],
                    "mergeable": pr_data["mergeable"],
                    "checks": checks,
                    "url": pr_data["html_url"]
                }
            else:
                logger.error(f"Failed to get PR status: {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Error getting PR status: {e}")
            return None

    async def merge_pr(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        commit_title: Optional[str] = None,
        commit_message: Optional[str] = None,
        merge_method: str = "squash"
    ) -> bool:
        """Merge a pull request."""
        try:
            await self._refresh_token()
            
            data = {"merge_method": merge_method}
            if commit_title:
                data["commit_title"] = commit_title
            if commit_message:
                data["commit_message"] = commit_message
            
            response = await self.client.put(
                f"/repos/{owner}/{repo}/pulls/{pr_number}/merge",
                json=data
            )
            
            if response.status_code == 200:
                logger.info(f"Merged PR #{pr_number}")
                return True
            else:
                logger.error(f"Failed to merge PR: {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Error merging PR: {e}")
            return False