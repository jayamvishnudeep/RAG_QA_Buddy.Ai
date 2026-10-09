"""Deploy QABuddy.ai to Vercel's free Hobby plan.

  python scripts/deploy_vercel.py --project qabuddy-vishnudeep-jayam            production
  python scripts/deploy_vercel.py --project qabuddy-vishnudeep-jayam --preview  preview URL only

Vercel runs neither Qdrant nor Ollama, so the deployment is read-only:

1. The Qdrant index is exported to a snapshot that the API searches in memory.
2. A slim copy of the app is built in .vercel-build/: the API (one Python
   function needing five packages), the web UI in public/ (served from the CDN),
   the config and the snapshot. Nothing else from this folder is uploaded.
3. Non-secret settings go to .vercel-build/.env. Questions are embedded with
   the same Qwen3-Embedding-0.6B model through Vercel's AI Gateway, which
   accepts the deployment's own OIDC token: no key, free monthly credits.
4. GROQ_API_KEY is read from your .env (never printed) and stored as an
   encrypted Vercel environment variable.
5. `vercel deploy` uploads the build.

Re-run it after `python -m qabuddy ingest` to publish new data.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
BUILD = APP / ".vercel-build"
sys.path.insert(0, str(APP))

PUBLIC_SETTINGS = {
    "QABUDDY_SNAPSHOT_DIR": "index_snapshot",
    "QABUDDY_STORAGE_DIR": "/tmp/qabuddy",
    "EMBED_API_URL": "https://ai-gateway.vercel.sh/v1",
    "EMBED_API_MODEL": "alibaba/qwen3-embedding-0.6b",
}
COPIED_SETTINGS = ("LLM_BASE_URL", "LLM_MODEL", "LLM_REASONING_EFFORT", "LLM_MAX_TOKENS", "TOP_K", "RETRIEVAL_CANDIDATES", "CONTEXT_TOKENS")
SECRETS = ("GROQ_API_KEY", "LLM_API_KEY", "QABUDDY_USERNAME", "QABUDDY_PASSWORD")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True, help="Vercel project name; also the <name>.vercel.app domain")
    parser.add_argument("--preview", action="store_true", help="deploy a preview instead of production")
    parser.add_argument("--skip-secrets", action="store_true", help="don't (re)upload secrets from .env")
    parser.add_argument("--no-deploy", action="store_true", help="build and link only")
    args = parser.parse_args()

    import yaml
    from dotenv import dotenv_values

    from qabuddy.prompts import MODES
    from qabuddy.settings import get_settings
    from qabuddy.snapshot import export_snapshot

    local = dotenv_values(APP / ".env")
    vercel = shutil.which("vercel") or shutil.which("vercel.cmd")
    if not vercel:
        print("The Vercel CLI is not installed: npm i -g vercel", file=sys.stderr)
        return 1

    print("1/5 Building .vercel-build/")
    BUILD.mkdir(exist_ok=True)
    for item in BUILD.iterdir():  # keep only the project link from earlier runs
        if item.name != ".vercel":
            shutil.rmtree(item) if item.is_dir() else item.unlink()
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(APP / "qabuddy", BUILD / "qabuddy", ignore=ignore)
    shutil.copytree(APP / "config", BUILD / "config")
    shutil.copytree(APP / "web", BUILD / "public")
    shutil.copytree(APP / "vercel" / "api", BUILD / "api", ignore=ignore)
    shutil.copy2(APP / "vercel" / "vercel.json", BUILD / "vercel.json")
    shutil.copy2(APP / "vercel" / "requirements.txt", BUILD / "requirements.txt")
    # `vercel link` writes .env.local with a short-lived OIDC token; it must never be uploaded.
    (BUILD / ".vercelignore").write_text(".env.local\n.env.*.local\n.vercel\n", encoding="utf-8")

    print("2/5 Exporting the index snapshot")
    # The UI's example questions and the eval questions are embedded now with the local
    # model, so they get full hybrid search even before the AI Gateway is enabled.
    seed = [q for mode in MODES.values() for q in mode.examples]
    questions = APP / "eval" / "questions.yaml"
    if questions.exists():
        seed += [item["q"] for item in yaml.safe_load(questions.read_text(encoding="utf-8"))["questions"]]
    meta = export_snapshot(get_settings(), BUILD / "index_snapshot", seed_questions=seed)
    print(f"    {meta['count']} chunks, {meta['dim']}-dim vectors, {meta['seeded_questions']} questions pre-embedded")

    print("3/5 Writing non-secret settings to .vercel-build/.env")
    settings = dict(PUBLIC_SETTINGS)
    settings.update({k: local[k] for k in COPIED_SETTINGS if local.get(k)})
    (BUILD / ".env").write_text("".join(f"{k}={v}\n" for k, v in settings.items()), encoding="utf-8")

    print(f"4/5 Linking Vercel project '{args.project}'")
    if not (BUILD / ".vercel" / "project.json").exists():
        subprocess.run([vercel, "project", "add", args.project], cwd=BUILD)  # fine if it already exists
        _run([vercel, "link", "--yes", "--project", args.project], cwd=BUILD)
    if not args.skip_secrets:
        targets = ["production"] if not args.preview else ["preview"]
        for name in SECRETS:
            value = (local.get(name) or "").strip()
            if not value:
                continue
            for target in targets:
                _run([vercel, "env", "add", name, target, "--sensitive", "--force", "--yes"], cwd=BUILD, stdin=value)
            print(f"    {name} stored as an encrypted variable ({', '.join(targets)})")

    if args.no_deploy:
        print("5/5 Skipped (--no-deploy). Deploy with: vercel deploy --prod  (in .vercel-build/)")
        return 0
    print("5/5 Deploying")
    command = [vercel, "deploy", "--yes"] + ([] if args.preview else ["--prod"])
    result = _run(command, cwd=BUILD, capture=True)
    print(f"\nDeployed: {_deployment_url(result.stdout)}")
    if not args.preview:
        print(f"Production: https://{args.project}.vercel.app (if the name was free)")
    return 0


def _deployment_url(output: str) -> str:
    """The CLI prints plain text to a terminal and JSON when run by a script or agent."""
    try:
        return json.loads(output[output.index("{") :])["deployment"]["url"]
    except (ValueError, KeyError, TypeError):
        urls = [line.strip() for line in output.splitlines() if line.strip().startswith("https://")]
        return urls[-1] if urls else output.strip()


def _run(command: list[str], cwd: Path, stdin: str | None = None, capture: bool = False):
    result = subprocess.run(command, cwd=cwd, input=stdin, text=True, capture_output=capture)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() if capture else ""
        raise SystemExit(f"Command failed ({result.returncode}): {' '.join(command[:3])} ... {detail}")
    return result


if __name__ == "__main__":
    sys.exit(main())
