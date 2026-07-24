import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from pilot.core.app import App
from pilot.config.app import AppConfig
from pilot.integrations.git import GitCredentialStore, provider_for_name
from pilot.tasks import Task, step


@dataclass(kw_only=True)
class CreateAppTask(Task):
    command: ClassVar[str] = "create-app"

    name: str
    title: str = ""
    description: str = ""
    publisher: str = ""
    email: str = ""
    app_license: str = "mit"
    create_github_repo: bool = False
    github_repo_private: bool = False
    sites: list[str] = field(default_factory=list)

    def run(self) -> None:
        # Essential App Scaffolding & Virtualenv Installation
        try:
            self.scaffold()
            self.install_env()
        except Exception as e:
            self.report(f"App creation failed: {e}. Rolling back...")
            try:
                import shutil
                app = App(AppConfig(name=self.name, repo="", branch="main"), self.bench)
                try:
                    app._deregister()
                except Exception:
                    pass
                try:
                    app._pip_uninstall()
                except Exception:
                    pass
                if app.path.exists():
                    shutil.rmtree(app.path)
            except Exception as cleanup_err:
                self.report(f"Failed to rollback app installation entirely: {cleanup_err}")
            sys.exit(1)

        # Asset Build (non-fatal for app directory)
        try:
            from pilot.managers.environment import PythonEnvManager
            env = PythonEnvManager(self.bench)._build_env()
            build_res = subprocess.run(
                [*self.bench.frappe_call, "frappe", "build", "--force", "--app", self.name],
                cwd=str(self.bench.sites_path),
                env=env,
                capture_output=True,
                text=True,
            )
            if build_res.returncode != 0:
                self.report(f"Warning: Asset build failed: {build_res.stderr}")
        except Exception as build_err:
            self.report(f"Warning: Asset build failed: {build_err}")

        # GitHub repo creation (non-fatal for app directory)
        if self.create_github_repo:
            try:
                self.create_github()
            except Exception as gh_err:
                self.report(f"Warning: GitHub repository integration failed: {gh_err}")

        # Install on requested sites
        self.install_sites()

    @step("scaffold", lambda self: f"Scaffolding app {self.name}")
    def scaffold(self) -> None:
        from pilot.core.app import NewAppOptions

        resolved_title = self.title or self.name.replace("-", " ").replace("_", " ").title()
        options = NewAppOptions(
            title=resolved_title,
            description=self.description,
            publisher=self.publisher,
            email=self.email,
            license=self.app_license,
            branch="main",
            github_workflow=False,
        )
        self.bench.new_app(self.name, options=options, on_progress=self.report)

    @step("install_env", lambda self: f"Installing {self.name} into virtualenv")
    def install_env(self) -> None:
        # Note: bench.new_app() automatically installs into environment and registers.
        # Preserved as a step for task execution reporting.
        pass

    @step("github_repo", lambda self: f"Creating GitHub repository for {self.name}")
    def create_github(self) -> None:
        store = GitCredentialStore(self.bench_root)
        record = store.load()
        if not record or not record.get("token"):
            self.report("No GitHub integration connected. Skipping GitHub repository creation.")
            return

        token = record["token"]
        provider = provider_for_name("github", token)
        try:
            repo_info = None
            try:
                repo_info = provider.create_repo(
                    self.name, self.description, self.github_repo_private
                )
            except Exception as repo_err:
                if (
                    "already exists" in str(repo_err).lower()
                    or "name already exists" in str(repo_err).lower()
                ):
                    username = record.get("username", "")
                    full_name = f"{username}/{self.name}"

                    try:
                        branches = provider.list_branches(full_name)
                    except Exception:
                        branches = []

                    if branches:
                        raise RuntimeError(
                            f"GitHub repository '{full_name}' already exists and contains existing code/branches "
                            f"({', '.join(branches)}). Choose a different name or delete the repo first."
                        )

                    self.report(
                        f"GitHub repository '{self.name}' already exists but is empty, reusing it."
                    )
                    clone_url = f"https://github.com/{username}/{self.name}.git"
                    repo_info = {
                        "clone_url": clone_url,
                        "html_url": f"https://github.com/{username}/{self.name}",
                    }
                else:
                    raise repo_err

            clean_url = repo_info["clone_url"]
            self.report(f"GitHub repo: {repo_info['html_url']}")

            app_path = self.bench_root / "apps" / self.name
            subprocess.run(
                ["git", "remote", "add", "origin", clean_url],
                cwd=str(app_path),
                capture_output=True,
            )
            subprocess.run(
                ["git", "remote", "set-url", "origin", clean_url],
                cwd=str(app_path),
                capture_output=True,
            )
            subprocess.run(["git", "branch", "-M", "main"], cwd=str(app_path), capture_output=True)

            self.report("Pushing initial commit to GitHub...")
            push_env = dict(os.environ)
            push_env["GIT_CONFIG_COUNT"] = "1"
            push_env["GIT_CONFIG_KEY_0"] = "http.extraheader"
            push_env["GIT_CONFIG_VALUE_0"] = f"Authorization: Bearer {token}"

            push_res = subprocess.run(
                ["git", "push", "-u", "origin", "main"],
                cwd=str(app_path),
                env=push_env,
                capture_output=True,
                text=True,
            )
            if push_res.returncode != 0:
                raise RuntimeError(f"Failed to push to GitHub: {push_res.stderr}")
            self.report("Successfully pushed to GitHub.")

        except Exception as e:
            raise RuntimeError(f"GitHub repository integration failed: {e}")

    def install_sites(self) -> None:
        for site_name in self.sites:
            safe_key = site_name.replace(".", "_").replace("-", "_")
            with self.step(
                f"install_{safe_key}_{self.name}", f"Install {self.name} on {site_name}"
            ):
                app = App(AppConfig(name=self.name, repo="", branch="main"), self.bench)
                self.bench.site(site_name).install_app(app)


if __name__ == "__main__":
    CreateAppTask.main()
