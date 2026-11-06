"""Commands for CI: check a change and report it on the pull request."""

import json
import os
from pathlib import Path

import typer

from .cli import app
from .cli_common import console, get_factory

ci_app = typer.Typer(help="Check a change in CI and report it on the pull request.", no_args_is_help=True)
app.add_typer(ci_app, name="ci")


def _pull_request_number() -> int | None:
    """The pull request of the workflow that is running, read from the event file that GitHub writes."""
    path = os.environ.get("GITHUB_EVENT_PATH")
    try:
        return int(json.loads(Path(path).read_text())["pull_request"]["number"]) if path else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _post(body: str, repo: str | None, pr: int | None) -> None:
    """Post the comment. A problem here is a warning: it must never change the verdict of the gate."""
    from .ci.github import GithubError, post_or_update
    token, repo, pr = os.environ.get("GITHUB_TOKEN"), repo or os.environ.get("GITHUB_REPOSITORY"), pr or _pull_request_number()
    if not (token and repo and pr):
        console.print("[yellow]Not posting the comment: GITHUB_TOKEN, the repository or the pull request number is missing.[/yellow]")
        return
    try:
        action, cid = post_or_update(token, repo, pr, body)
        console.print(f"Comment {action} on {repo}#{pr} (comment {cid}).")
    except GithubError as e:
        console.print(f"[yellow]Could not post the comment: {e}[/yellow]")


@ci_app.command("check")
def ci_check(
    config: Path = typer.Option(Path("trustbench.yaml"), help="The config file"),
    target: str = typer.Option(None, "--target", "-t", help="The candidate target (a name in the config)"),
    baseline_target: str = typer.Option(None, "--baseline-target", help="A target to compare with. It runs first, on the same cases."),
    policy: Path = typer.Option(None, help="Release policy (default: the 'policy' of the config)"),
    min_effect: float = typer.Option(0.01, help="Smallest change in pass rate that matters"),
    comment_file: Path = typer.Option(Path("trustbench-comment.md"), help="Where to write the comment"),
    result_file: Path = typer.Option(Path("trustbench-result.json"), help="Where to write the result as JSON"),
    post: bool = typer.Option(None, "--post/--no-post", help="Post the comment on the pull request (default: only in a pull_request workflow)"),
    repo: str = typer.Option(None, help="owner/name (default: GITHUB_REPOSITORY)"),
    pr: int = typer.Option(None, help="Pull request number (default: from the workflow event)"),
):
    """Run the candidate, check it against the policy, write the comment, and post it. Exit code 1 blocks the merge."""
    from .ci.check import CheckError, run_check
    from .edd.config import ConfigError, load_config
    from .providers import allow_app_targets
    allow_app_targets()
    try:
        cfg = load_config(config)
        if cfg is None:
            raise ConfigError(f"Config file not found: {config}. Create one with 'trustbench init'.")
        res = run_check(get_factory(), cfg, target=target, baseline_target=baseline_target, policy_path=policy, min_effect=min_effect)
    except (ConfigError, CheckError, ValueError) as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(2)                 # Exit code 2 means the check could not run. Exit code 1 means "the gate says no".
    comment_file.write_text(res.comment)
    result_file.write_text(json.dumps(res.to_dict(), indent=2))
    if os.environ.get("GITHUB_STEP_SUMMARY"):          # Visible on every run, also for forks that cannot comment.
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(res.comment + "\n")
    if post if post is not None else os.environ.get("GITHUB_EVENT_NAME") == "pull_request":
        _post(res.comment, repo, pr)
    rate = "-" if res.pass_rate is None else f"{res.pass_rate:.1%}"
    console.print(f"Run {res.run_id}: pass rate {rate}. Gate: {res.status or 'no policy, so nothing was enforced'}. "
                  f"Comment: {comment_file}")
    raise typer.Exit(0 if res.allowed else 1)


@ci_app.command("comment")
def ci_comment(run: str = typer.Argument(..., help="Run id (or baseline name)"),
               baseline: str = typer.Option(None, "--baseline", "-b", help="Run id or baseline name to compare with"),
               policy: Path = typer.Option(None, help="Release policy file"),
               out: Path = typer.Option(None, help="Write the comment here (default: print it)"),
               min_effect: float = typer.Option(0.01)):
    """Build the pull request comment for a run that already exists. The command posts nothing."""
    from . import baselines
    from .gate import evaluate_gate
    from .ci.comment import build_comment
    from .policy import PolicyError, load_policy
    from .rundata import load_run
    factory = get_factory()
    try:
        run_id, model = baselines.resolve(factory, run)
        cand = load_run(factory, run_id)
        base_ref = baselines.resolve(factory, baseline) if baseline else None
        base = load_run(factory, base_ref[0]) if base_ref else None
        pol = load_policy(policy) if policy else None
        decision = evaluate_gate(pol, cand, model, base, base_ref[1] if base_ref else None) if pol else None
        text = build_comment(cand, model, baseline=base, baseline_model=base_ref[1] if base_ref else None, decision=decision,
                             policy=pol, min_effect=min_effect)
    except (ValueError, PolicyError) as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    if out:
        out.write_text(text)
        console.print(f"Wrote {out}.")
    else:
        typer.echo(text)


@ci_app.command("post")
def ci_post(file: Path = typer.Argument(..., help="A comment made by 'ci check' or 'ci comment'"),
            repo: str = typer.Option(None, help="owner/name (default: GITHUB_REPOSITORY)"),
            pr: int = typer.Option(None, help="Pull request number (default: from the workflow event)")):
    """Post a comment file on a pull request, or update the one posted before. Reads GITHUB_TOKEN."""
    if not file.is_file():
        console.print(f"[red]{file} not found.[/red]")
        raise typer.Exit(1)
    _post(file.read_text(), repo, pr)
