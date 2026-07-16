import argparse
import json

from pilot.commands.setup.letsencrypt import SetupLetsEncryptCommand
from pilot.config.toml_store import BenchTomlStore
from pilot.internal.atomic_file import exclusive_file_lock, replace_private_text_locked
from .base_task import BaseTask


class SetupLetsEncryptTask(BaseTask):
    @classmethod
    def _parser(cls) -> argparse.ArgumentParser:
        parser = super()._parser()
        parser.add_argument("--site", default="")
        parser.add_argument("--email", default="")
        return parser

    def __init__(self, bench, bench_root, args):
        super().__init__(bench, bench_root, args)
        self.site = args.site
        self.email = args.email

    def run(self) -> None:
        self._step("letsencrypt", "Set up Let's Encrypt")
        self._require_production_privileges()
        self._apply_email()
        self._enable_site_tls()
        SetupLetsEncryptCommand(self.bench).run()
        self._step("done")

    def _apply_email(self) -> None:
        if not self.email:
            return
        with BenchTomlStore.for_bench(self.bench_root).edit() as config:
            config.letsencrypt.email = self.email
        self.bench.config.letsencrypt.email = self.email

    def _enable_site_tls(self) -> None:
        if not self.site:
            return
        sites_root = (self.bench_root / "sites").resolve()
        site_path = sites_root / self.site
        config_path = site_path / "site_config.json"
        if (
            (self.bench_root / "sites").is_symlink()
            or site_path.is_symlink()
            or site_path.resolve(strict=False).parent != sites_root
            or config_path.is_symlink()
            or not config_path.is_file()
        ):
            raise ValueError("Site configuration is unavailable.")
        with exclusive_file_lock(config_path):
            config = json.loads(config_path.read_text())
            if not isinstance(config, dict):
                raise ValueError("Site configuration must be a JSON object.")
            config["ssl"] = True
            replace_private_text_locked(config_path, json.dumps(config, indent=1))


if __name__ == "__main__":
    SetupLetsEncryptTask.main()
