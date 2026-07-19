import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
import urllib.request
import urllib.error

from pilot.tasks.jobs.base_task import BaseTask
from pilot.integrations.git import GitCredentialStore, provider_for_name
from pilot.core.app import App
from pilot.config.app import AppConfig

class CreateAppTask(BaseTask):
    @classmethod
    def _parser(cls):
        p = super()._parser()
        p.add_argument("name")
        p.add_argument("--title", default="")
        p.add_argument("--description", default="")
        p.add_argument("--publisher", default="")
        p.add_argument("--email", default="")
        p.add_argument("--app-license", default="mit")
        p.add_argument("--create-github-repo", action="store_true")
        p.add_argument("--github-repo-private", action="store_true")
        p.add_argument("--sites", nargs="*", default=[])
        return p

    def __init__(self, bench, bench_root, args):
        super().__init__(bench, bench_root, args)
        self.name = args.name
        self.title = args.title or self.name.replace("_", " ").title()
        self.description = args.description
        self.publisher = args.publisher
        self.email = args.email
        self.app_license = args.app_license
        self.create_github_repo = args.create_github_repo
        self.github_repo_private = args.github_repo_private
        self.sites = args.sites or []

    def run(self) -> None:
        self._step("scaffold", f"Scaffolding app {self.name}")
        
        # We need to construct the hooks dictionary equivalent to what
        # frappe.utils.boilerplate expects and write the files.
        # To run it inside the bench context, we'll invoke a python snippet
        # using the bench environment python to run the make_boilerplate function.
        apps_dir = self.bench_root / "apps"
        
        # Prepare python snippet
        python_bin = str(self.bench_root / "env" / "bin" / "python")
        
        hooks_data = {
            "app_name": self.name,
            "app_title": self.title,
            "app_description": self.description,
            "app_publisher": self.publisher,
            "app_email": self.email,
            "app_license": self.app_license,
            "create_github_workflow": False,
            "branch_name": "main",
        }
        
        # Use python inline execution to generate app boilerplate
        snippet = f"""
import sys
import os
sys.path.append(os.path.abspath('apps/frappe'))
import frappe
from frappe.utils.boilerplate import _create_app_boilerplate

class HooksObject:
    def __init__(self, d):
        self.__dict__.update(d)
    def __getattr__(self, key):
        return self.__dict__.get(key)
    def __setitem__(self, key, val):
        self.__dict__[key] = val
    def __getitem__(self, key):
        return self.__dict__.get(key)
    def keys(self):
        return self.__dict__.keys()

hooks = HooksObject({repr(hooks_data)})
_create_app_boilerplate('apps', hooks, no_git=False)
"""
        
        self._report(f"Invoking Frappe boilerplate creator for {self.name}...")
        r = subprocess.run([python_bin, "-c", snippet], cwd=str(self.bench_root), capture_output=True, text=True)
        if r.returncode != 0:
            self._report(f"Boilerplate creation failed: {r.stderr}")
            sys.exit(1)
            
        self._report(f"App boilerplate created.")
        
        # Now install the app into the virtual environment
        self._step("install_env", f"Installing {self.name} into virtualenv")
        app = App(AppConfig(name=self.name, repo="", branch="main"), self.bench)
        app._install_into_environment()
        app._register()
        
        # Build assets using bench/frappe call
        subprocess.run([*self.bench.frappe_call, "frappe", "build", "--force", "--app", self.name], cwd=str(self.bench_root))
        
        # GitHub repo creation
        if self.create_github_repo:
            self._step("github_repo", f"Creating GitHub repository for {self.name}")
            store = GitCredentialStore(self.bench_root)
            record = store.load()
            if record and record.get("token"):
                token = record["token"]
                provider = provider_for_name("github", token)
                try:
                    repo_info = None
                    try:
                        repo_info = provider.create_repo(self.name, self.description, self.github_repo_private)
                    except Exception as repo_err:
                        if "already exists" in str(repo_err).lower() or "name already exists" in str(repo_err).lower():
                            username = record.get("username", "")
                            full_name = f"{username}/{self.name}"
                            
                            # Check if the repo has any branches (is it empty?)
                            try:
                                branches = provider.list_branches(full_name)
                            except Exception:
                                branches = []
                                
                            if branches:
                                raise RuntimeError(f"GitHub repository '{full_name}' already exists and contains existing code/branches ({', '.join(branches)}). Choose a different name or delete the repo first.")
                                
                            self._report(f"GitHub repository '{self.name}' already exists but is empty, reusing it.")
                            clone_url = f"https://github.com/{username}/{self.name}.git"
                            repo_info = {
                                "clone_url": clone_url,
                                "html_url": f"https://github.com/{username}/{self.name}"
                            }
                        else:
                            raise repo_err
                            
                    clone_url = repo_info["clone_url"]
                    self._report(f"GitHub repo: {repo_info['html_url']}")
                    
                    # Add clean remote
                    app_path = apps_dir / self.name
                    subprocess.run(["git", "remote", "add", "origin", clone_url], cwd=str(app_path))
                    
                    # Rename branch to main if needed
                    subprocess.run(["git", "branch", "-M", "main"], cwd=str(app_path))
                    
                    # Push to origin securely without exposing token in git remote url or process list
                    self._report(f"Pushing initial commit to GitHub...")
                    extra_header = f"Authorization: token {token}"
                    push_res = subprocess.run([
                        "git",
                        "-c", f"http.extraHeader={extra_header}",
                        "push",
                        "-u",
                        "origin",
                        "main"
                    ], cwd=str(app_path), capture_output=True, text=True)
                    if push_res.returncode != 0:
                        raise RuntimeError(f"Failed to push to GitHub: {push_res.stderr}")
                    else:
                        self._report(f"Successfully pushed to GitHub.")
                    
                except Exception as e:
                    self._report(f"GitHub repository integration failed: {e}")
                    sys.exit(1)
            else:
                self._report("No GitHub integration connected. Skipping GitHub repository creation.")
                
        # Install on requested sites
        for site in self.sites:
            safe_key = site.replace(".", "_").replace("-", "_")
            self._step(f"install_{safe_key}_{self.name}", f"Install {self.name} on {site}")
            from pilot.core.site import Site, SiteConfig
            Site(SiteConfig(name=site, apps=[]), self.bench).install_app(app)
            
        self._step("done")

if __name__ == "__main__":
    CreateAppTask.main()
