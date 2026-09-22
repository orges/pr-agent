"""Route a small or path-shaped pull request to a different primary model.

The [model_routing] settings hold an ordered list of rules, each naming a model and the largest
pull request it takes, measured in diff hunks and changed files. Both counts come from the
provider's diff, so they do not depend on which model's tokenizer would count the tokens.

Rules can also match on the changed paths: `include_paths` (glob patterns) makes a rule apply
when at least `min_share` (default 0.6) of the changed files match, and `exclude_paths` removes
files from that count - or, when used alone, makes the rule apply only when no changed file
matches any excluded pattern. Size and path predicates combine with AND; the first rule whose
predicates all hold wins, and a pull request that fits no rule keeps the configured primary.
"""
from __future__ import annotations

from fnmatch import fnmatch
from typing import List, Optional, Tuple

from pr_agent.algo.types import FilePatchInfo
from pr_agent.algo.utils import ModelType
from pr_agent.config_loader import get_settings
from pr_agent.log import get_logger


def count_hunks(diff_files: List[FilePatchInfo]) -> int:
    """Count the diff hunks across the files. A patch without hunk headers still counts as one."""
    total = 0
    for diff_file in diff_files:
        patch = getattr(diff_file, "patch", "") or ""
        if not patch.strip():
            continue
        total += sum(1 for line in patch.splitlines() if line.startswith("@@")) or 1
    return total


def _limit(rule, key: str) -> Optional[int]:
    value = rule.get(key)
    if value is None or value == "":
        return None
    return int(value)


def _matches_any_pattern(filename: str, patterns) -> bool:
    """True when the full path or its basename matches any glob pattern."""
    base = filename.rsplit("/", 1)[-1]
    return any(fnmatch(filename, str(p)) or fnmatch(base, str(p)) for p in (patterns or []))


def _path_share(diff_files, include_patterns, exclude_patterns) -> Optional[float]:
    """Fraction of changed files matching include_patterns, ignoring excluded files."""
    matched = total = 0
    for diff_file in diff_files:
        filename = getattr(diff_file, "filename", "") or ""
        if not filename:
            continue
        total += 1
        if exclude_patterns and _matches_any_pattern(filename, exclude_patterns):
            continue
        if _matches_any_pattern(filename, include_patterns):
            matched += 1
    if total == 0:
        return None
    return matched / total


def route_primary_model(model_type: ModelType, git_provider) -> Optional[Tuple[str, Optional[str]]]:
    """Return the (model, deployment_id) a routing rule picks for this pull request.

    None keeps the configured primary. Only a call for the regular model is routed: a tool that
    asked for the weak or the reasoning tier made that choice deliberately. Rules are checked in
    order and the first one whose limits the pull request fits wins.
    """
    if model_type != ModelType.REGULAR or git_provider is None:
        return None
    settings = get_settings()
    if not settings.get("model_routing.enable", False):
        return None
    rules = settings.get("model_routing.rules", None) or []
    if not rules:
        return None

    diff_files = git_provider.get_diff_files()
    num_files = len(diff_files)
    num_hunks = count_hunks(diff_files)
    size = f"{num_hunks} hunks in {num_files} files"
    global_deployment_id = settings.get("openai.deployment_id", None)

    for rule in rules:
        try:
            model = rule.get("model")
            max_hunks = _limit(rule, "max_hunks")
            max_files = _limit(rule, "max_files")
            include_paths = rule.get("include_paths") or []
            exclude_paths = rule.get("exclude_paths") or []
        except (AttributeError, TypeError, ValueError):
            model = max_hunks = max_files = None
            include_paths = exclude_paths = []
        has_path_predicate = bool(include_paths or exclude_paths)
        if not model or (max_hunks is None and max_files is None and not has_path_predicate):
            get_logger().warning(f"Ignoring model routing rule without a model or a predicate: {rule}")
            continue
        if max_hunks is not None and num_hunks > max_hunks:
            continue
        if max_files is not None and num_files > max_files:
            continue
        path_note = ""
        if include_paths:
            try:
                min_share = float(rule.get("min_share") or 0.6)
            except (TypeError, ValueError):
                min_share = 0.6
            share = _path_share(diff_files, include_paths, exclude_paths)
            if share is None or share < min_share:
                continue
            path_note = f", {int(round(share * 100))}% path match"
        elif exclude_paths:
            if any(_matches_any_pattern(getattr(f, "filename", "") or "", exclude_paths) for f in diff_files):
                continue
            path_note = ", no excluded paths"
        deployment_id = rule.get("deployment_id") or None
        if global_deployment_id and not deployment_id:
            get_logger().warning(f"Model routing rule for '{model}' has no deployment_id while "
                                 f"openai.deployment_id is set, skipping it")
            continue
        get_logger().info(f"Model routing: {size}{path_note}, using '{model}' as the primary model")
        return model, deployment_id

    get_logger().info(f"Model routing: {size} fit no rule, keeping the configured primary model")
    return None
