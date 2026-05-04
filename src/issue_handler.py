#!/usr/bin/env python3
"""Issue handler: fetches the oldest unprioritized Issue and implements a fix via Claude Code CLI."""

import os
import subprocess
import sys
from typing import Optional

import requests

GITHUB_API = "https://api.github.com"
PRIORITY_LABELS = {"priority:high", "priority:medium", "priority:low", "critical"}


def github_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def get_oldest_unprioritized_issue(token: str, repo: str) -> Optional[dict]:
    """Return the oldest open Issue that has no explicit priority label."""
    url = f"{GITHUB_API}/repos/{repo}/issues"
    params = {"state": "open", "per_page": 100, "sort": "created", "direction": "asc"}
    res = requests.get(url, params=params, headers=github_headers(token))
    res.raise_for_status()
    for issue in res.json():
        if "pull_request" in issue:
            continue
        labels = {lbl["name"] for lbl in issue.get("labels", [])}
        if labels & PRIORITY_LABELS:
            continue
        if "in-progress" in labels:
            continue
        return issue
    return None


def set_label(token: str, repo: str, issue_number: int, label: str, add: bool) -> None:
    if add:
        url = f"{GITHUB_API}/repos/{repo}/issues/{issue_number}/labels"
        requests.post(url, json={"labels": [label]}, headers=github_headers(token))
    else:
        url = f"{GITHUB_API}/repos/{repo}/issues/{issue_number}/labels/{label}"
        requests.delete(url, headers=github_headers(token))


def handle_oldest_issue(token: str, repo: str) -> None:
    issue = get_oldest_unprioritized_issue(token, repo)
    if not issue:
        print("対応するIssueがありません。")
        return

    issue_number: int = issue["number"]
    issue_title: str = issue["title"]
    issue_body: str = issue.get("body") or ""
    print(f"Issue #{issue_number} を処理します: {issue_title}")

    set_label(token, repo, issue_number, "in-progress", add=True)
    try:
        _invoke_claude_code(issue_number, issue_title, issue_body)
    finally:
        set_label(token, repo, issue_number, "in-progress", add=False)


def _invoke_claude_code(issue_number: int, title: str, body: str) -> None:
    prompt = (
        f"以下のGitHub Issueを解決してください。\n\n"
        f"## Issue #{issue_number}: {title}\n\n{body}\n\n"
        "作業手順:\n"
        "1. リポジトリ構成と関連ファイルを確認する\n"
        f"2. 作業ブランチを作成する: git checkout -b fix/issue-{issue_number}\n"
        "3. コードを実装する\n"
        "4. テストとlintを実行し、失敗したら修正して再実行する\n"
        "5. git add / git commit / git push する\n"
        "6. gh pr create でPRを作成する\n\n"
        "コミットメッセージとPRのタイトル・本文は日本語で記述してください。"
    )

    result = subprocess.run(
        ["claude", "-p", prompt, "--allowedTools", "Bash,Read,Write,Edit"],
        timeout=1800,
    )
    if result.returncode != 0:
        print(f"Claude Code が非正常終了しました (終了コード: {result.returncode})", file=sys.stderr)


if __name__ == "__main__":
    _token = os.environ["GITHUB_TOKEN"]
    _repo = os.environ["GITHUB_REPOSITORY"]
    handle_oldest_issue(_token, _repo)
