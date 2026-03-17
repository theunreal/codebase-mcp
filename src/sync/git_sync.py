"""
Git sync — clones and pulls repos defined in config.
"""

from pathlib import Path

import structlog
from git import Repo as GitRepo
from git.exc import GitCommandError, InvalidGitRepositoryError

from src.config import RepoConfig

logger = structlog.get_logger()


def sync_repo(repo_config: RepoConfig, repos_dir: str) -> str:
    """
    Ensure a repo is available locally and up to date.
    Returns the local path to the repo.

    - If repo_config.path is set, use it directly (already cloned).
    - If repo_config.url is set, clone to repos_dir/<name> or pull if exists.
    """
    if repo_config.path:
        local_path = Path(repo_config.path)
        if not local_path.exists():
            raise FileNotFoundError(f"Local repo path does not exist: {repo_config.path}")
        # Try to pull if it's a git repo
        try:
            git_repo = GitRepo(str(local_path))
            git_repo.remotes.origin.pull()
            logger.info("pulled_local_repo", name=repo_config.name, path=str(local_path))
        except (InvalidGitRepositoryError, GitCommandError, ValueError):
            logger.info("using_local_path", name=repo_config.name, path=str(local_path))
        return str(local_path)

    if not repo_config.url:
        raise ValueError(f"Repo {repo_config.name} has neither path nor url configured")

    target_dir = Path(repos_dir) / repo_config.name
    target_dir.parent.mkdir(parents=True, exist_ok=True)

    if target_dir.exists():
        # Already cloned — pull latest
        try:
            git_repo = GitRepo(str(target_dir))
            git_repo.remotes.origin.pull()
            logger.info("pulled_repo", name=repo_config.name, branch=repo_config.branch)
        except (GitCommandError, InvalidGitRepositoryError) as e:
            logger.warning("pull_failed", name=repo_config.name, error=str(e))
    else:
        # Clone
        logger.info("cloning_repo", name=repo_config.name, url=repo_config.url)
        GitRepo.clone_from(
            repo_config.url,
            str(target_dir),
            branch=repo_config.branch,
        )
        logger.info("cloned_repo", name=repo_config.name)

    return str(target_dir)


def sync_all(repos: list[RepoConfig], repos_dir: str) -> dict[str, str]:
    """Sync all repos. Returns {repo_name: local_path}."""
    paths: dict[str, str] = {}
    for repo in repos:
        try:
            local_path = sync_repo(repo, repos_dir)
            paths[repo.name] = local_path
        except Exception as e:
            logger.error("sync_failed", repo=repo.name, error=str(e))
    return paths
