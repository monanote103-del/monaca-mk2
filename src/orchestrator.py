#!/usr/bin/env python3
"""Orchestrator: PRレビュー対応を優先し、なければ最古のIssueを処理する。"""

import os
import sys

import requests

from issue_handler import handle_oldest_issue
from review_handler import handle_review

GITHUB_API = "https://api.github.com"


def github_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def get_unhandled_review_requests(token: str, repo: str) -> list[dict]:
    """CHANGES_REQUESTEDが未対応（AIサマリー未投稿）のPRとレビューを返す。"""
    url = f"{GITHUB_API}/repos/{repo}/pulls"
    res = requests.get(
        url, params={"state": "open", "per_page": 100}, headers=github_headers(token)
    )
    res.raise_for_status()

    pending = []
    for pr in res.json():
        pr_number = pr["number"]

        reviews = requests.get(
            f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}/reviews",
            headers=github_headers(token),
        ).json()

        changes_requested = [
            r for r in reviews
            if r.get("state") == "CHANGES_REQUESTED"
            and r.get("user", {}).get("type") != "Bot"
        ]
        if not changes_requested:
            continue

        comments = requests.get(
            f"{GITHUB_API}/repos/{repo}/issues/{pr_number}/comments",
            headers=github_headers(token),
        ).json()

        already_handled = any(
            "[AI Review Summary]" in c.get("body", "") for c in comments
        )
        if already_handled:
            continue

        pending.append({"pr": pr, "review": changes_requested[0]})

    return pending


def main() -> None:
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")

    if not token or not repo:
        print("GITHUB_TOKEN / GITHUB_REPOSITORY が未設定です。", file=sys.stderr)
        sys.exit(1)

    print("未対応のPRレビューを確認中...")
    pending = get_unhandled_review_requests(token, repo)

    if pending:
        print(f"未対応レビューが {len(pending)} 件あります。レビュー対応を優先します。")
        for item in pending:
            pr = item["pr"]
            review = item["review"]
            print(f"  PR #{pr['number']}: {pr['title']}")
            handle_review({"review": review, "pull_request": pr})
    else:
        print("未対応レビューなし。最古のIssueを処理します。")
        handle_oldest_issue(token, repo)


if __name__ == "__main__":
    main()
