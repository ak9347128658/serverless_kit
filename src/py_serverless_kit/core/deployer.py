"""High level package / deploy / remove orchestration used by the CLI."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..packaging.builder import build_artifact
from ..providers.base import Artifact, Issue, RenderContext
from ..terraform.runner import TerraformRunner
from .describe import RECORD_FILE, build_record
from .project import Project

Echo = Callable[[str], None]


class DeployError(Exception):
    pass


@dataclass
class PackageResult:
    artifact: Artifact
    tf_file: Path
    resource_count: int
    warnings: List[str] = field(default_factory=list)


class Deployer:
    def __init__(self, project: Project, echo: Echo = print, runner: Optional[TerraformRunner] = None) -> None:
        self.project = project
        self.echo = echo
        self._runner = runner

    # ---------------------------------------------------------------- helpers
    @property
    def runner(self) -> TerraformRunner:
        if self._runner is None:
            tf = self.project.config.provider.terraform
            self._runner = TerraformRunner(self.project.work_dir, binary=tf.binary, echo=self.echo)
        return self._runner

    def check(self) -> List[Issue]:
        issues = self.project.validate()
        errors = [i for i in issues if i.level == "error"]
        for issue in issues:
            self.echo(f"  {issue}")
        if errors:
            raise DeployError(f"{len(errors)} configuration error(s); fix them and retry")
        return issues

    def run_hook(self, name: str) -> None:
        commands = self.project.config.hooks.get(name) or []
        for cmd in commands:
            self.echo(f"  hook {name}: {cmd}")
            env = dict(os.environ)
            env.update(
                {
                    "PSKIT_SERVICE": self.project.config.service,
                    "PSKIT_STAGE": self.project.config.provider.stage,
                    "PSKIT_PROVIDER": self.project.config.provider.name,
                    "PSKIT_WORK_DIR": str(self.project.work_dir),
                }
            )
            proc = subprocess.run(cmd, shell=True, cwd=self.project.root, env=env)  # noqa: S602 - user hook
            if proc.returncode != 0:
                raise DeployError(f"hook {name} failed ({proc.returncode}): {cmd}")

    # ---------------------------------------------------------------- steps
    def package(self, *, skip_deps: bool = False) -> PackageResult:
        project = self.project
        self.check()
        project.work_dir.mkdir(parents=True, exist_ok=True)
        _write_gitignore(project.root / ".pskit")
        self.run_hook("before_package")
        self.echo(
            f"Packaging {project.config.service} for {project.provider.display_name} ({project.config.provider.stage})"
        )
        artifact = build_artifact(project, skip_deps=skip_deps, log=self.echo)
        ctx = RenderContext(
            config=project.config,
            provider=project.provider,
            root=project.root,
            work_dir=project.work_dir,
            artifact=artifact,
            region=project.region,
            workflow_engines=dict(project.engines),
        )
        doc = project.provider.render(ctx)
        tf_file = project.work_dir / "main.tf.json"
        tf_file.write_text(doc.to_json(), "utf-8")
        warnings = list(getattr(doc, "warnings", []))
        for w in warnings:
            self.echo(f"  WARNING: {w}")
        self.echo(f"  terraform: {doc.count()} resource(s) -> {tf_file.relative_to(project.root)}")
        self.run_hook("after_package")
        return PackageResult(artifact=artifact, tf_file=tf_file, resource_count=doc.count(), warnings=warnings)

    def plan(self, *, skip_deps: bool = False, destroy: bool = False) -> bool:
        self.package(skip_deps=skip_deps)
        self.runner.init()
        return self.runner.plan(destroy=destroy, out=None)

    def deploy(self, *, skip_deps: bool = False, auto_approve: bool = True, upgrade: bool = False) -> Dict[str, Any]:
        result = self.package(skip_deps=skip_deps)
        self.run_hook("before_deploy")
        self.echo("Running terraform init")
        self.runner.init(upgrade=upgrade)
        if auto_approve:
            self.echo("Running terraform apply")
            self.runner.apply(auto_approve=True)
        else:
            changed = self.runner.plan(out="tfplan")
            if not changed:
                self.echo("No changes.")
            else:
                self.runner.apply(plan_file="tfplan")
        outputs = self.runner.output()
        record = build_record(
            self.project,
            outputs,
            artifact={
                "sha256": result.artifact.sha256,
                "size": result.artifact.size,
                "files": result.artifact.file_count,
            },
        )
        self.save_record(record)
        self.run_hook("after_deploy")
        return record

    def remove(self, *, auto_approve: bool = True, keep_resources: bool = False) -> List[str]:
        """Destroy the stage. With ``keep_resources`` managed buckets, queues and topics stay in the
        cloud (they are dropped from the Terraform state first). Returns the kept addresses."""
        from .retain import kept_addresses, save_kept, supports

        project = self.project
        if not (project.work_dir / "main.tf.json").is_file():
            # Re-render so terraform knows the provider configuration.
            self.package(skip_deps=True)
        self.run_hook("before_remove")
        self.runner.init()
        kept: List[str] = []
        if keep_resources:
            provider = project.config.provider.name
            if not supports(provider):
                self.echo(f"  {project.provider.display_name}: no managed buckets, queues or topics to keep")
            else:
                kept = kept_addresses(provider, self.runner.state_list())
                if kept:
                    # Remember them in .pskit/<p>-<s>/kept/ so `pskit resources delete` can remove them later.
                    save_kept(project.work_dir, self.runner.state_pull(), kept)
                for address in kept:
                    self.echo(f"  keeping {address} (delete later with `pskit resources delete`)")
                self.runner.state_rm(kept)
        self.runner.destroy(auto_approve=auto_approve)
        record_path = project.work_dir / RECORD_FILE
        if record_path.is_file():
            record = json.loads(record_path.read_text("utf-8"))
            record["status"] = "removed"
            if kept:
                record["kept_resources"] = kept
            record_path.write_text(json.dumps(record, indent=2), "utf-8")
        self.run_hook("after_remove")
        return kept

    # ---------------------------------------------------------------- data resources
    def cloud_resources(self) -> Dict[str, List[Dict[str, Any]]]:
        """Buckets, queues and topics pskit created: still deployed, and kept by ``remove --keep-resources``."""
        from .retain import data_resources, list_kept

        provider = self.project.config.provider.name
        deployed: List[Dict[str, Any]] = []
        if (self.project.work_dir / "main.tf.json").is_file():
            self.runner.init()
            deployed = data_resources(provider, self.runner.state_pull())
        return {"deployed": deployed, "kept": list_kept(self.project.work_dir, provider)}

    def delete_resources(self, addresses: List[str]) -> List[str]:
        """Delete buckets, queues or topics by Terraform address (kept ones or deployed ones). Returns deleted."""
        from .retain import delete_kept

        found = self.cloud_resources()
        kept = {r["address"] for r in found["kept"]}
        deployed = {r["address"] for r in found["deployed"]}
        unknown = [a for a in addresses if a not in kept and a not in deployed]
        if unknown:
            raise DeployError(
                f"not a bucket, queue or topic of this deployment: {', '.join(unknown)} (see `pskit resources list`)"
            )
        done: List[str] = []
        from_kept = [a for a in addresses if a in kept]
        if from_kept:
            tf = self.project.config.provider.terraform
            delete_kept(self.project.work_dir, from_kept, binary=tf.binary, echo=self.echo)
            done += from_kept
        targets = [a for a in addresses if a in deployed and a not in kept]
        if targets:
            self.runner.destroy(auto_approve=True, targets=targets)
            done += targets
            self.echo(
                "  Deleted from the cloud. If it is still in pskit.yml, the next deploy creates it again: "
                "remove it from `resources:` / the trigger first."
            )
        return done

    def refresh_info(self) -> Dict[str, Any]:
        outputs = self.runner.output()
        previous = self.load_record() or {}
        record = build_record(self.project, outputs, artifact=previous.get("artifact"))
        self.save_record(record)
        return record

    # ---------------------------------------------------------------- record
    def save_record(self, record: Dict[str, Any]) -> Path:
        path = self.project.work_dir / RECORD_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2, default=str), "utf-8")
        return path

    def load_record(self) -> Optional[Dict[str, Any]]:
        path = self.project.work_dir / RECORD_FILE
        if path.is_file():
            return json.loads(path.read_text("utf-8"))
        return None


def _write_gitignore(pskit_dir: Path) -> None:
    pskit_dir.mkdir(parents=True, exist_ok=True)
    gi = pskit_dir / ".gitignore"
    if not gi.exists():
        gi.write_text(
            "# Generated by py-serverless-kit. Keep terraform state out of git -\n"
            "# configure a remote backend (provider.state) for team use.\n*\n",
            "utf-8",
        )
