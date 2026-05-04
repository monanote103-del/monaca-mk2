#!/usr/bin/env python3
"""Issue handler: fetches the oldest unprioritized Issue and implements a fix via Claude tool use."""

import json
import os
import subprocess
import sys
from typing import Optional

import anthropic
import requests

GITHUB_API = "https://api.github.com"
PRIORITY_LABELS = {"priority:high", "priority:medium", "priority:low", "critical"}
MAX_AGENT_ITERATIONS = 30

TOOLS: list[dict] = [
    {
        "name": "list_files",
        "description": "List files in a directory of the repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path relative to repo root (e.g. '.' or 'src')"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "read_file",
        "description": "Read the content of a file in the repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to repo root"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write (create or overwrite) a file in the repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to repo root"},
                "content": {"type": "string", "description": "Full file content"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "run_bash",
        "description": (
            "Run a bash command in the repository root. "
            "Use for git operations (checkout, add, commit, push), "
            "running tests, lint, etc. Returns stdout+stderr and exit code."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Bash command to execute"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "create_pull_request",
        "description": "Create a GitHub pull request from the current branch to the default branch.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "PR title (Japanese)"},
                "body": {"type": "string", "description": "PR description in Markdown (Japanese)"},
                "branch": {"type": "string", "description": "Head branch name that has been pushed"},
            },
            "required": ["title", "body", "branch"],
        },
    },
]


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


def get_default_branch(token: str, repo: str) -> str:
    res = requests.get(f"{GITHUB_API}/repos/{repo}", headers=github_headers(token))
    return res.json().get("default_branch", "main")


def execute_tool(name: str, inputs: dict, token: str, repo: str) -> str:
    if name == "list_files":
        path = inputs["path"]
        result = subprocess.run(["ls", "-la", path], capture_output=True, text=True)
        return result.stdout or result.stderr or "(empty)"

    if name == "read_file":
        path = inputs["path"]
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError as e:
            return f"Error reading file: {e}"

    if name == "write_file":
        path = inputs["path"]
        content = inputs["content"]
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Written: {path}"

    if name == "run_bash":
        command = inputs["command"]
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=300
        )
        out = result.stdout
        if result.stderr:
            out += f"\nSTDERR:\n{result.stderr}"
        if result.returncode != 0:
            out += f"\n[exit code {result.returncode}]"
        return out or "(no output)"

    if name == "create_pull_request":
        title = inputs["title"]
        body = inputs["body"]
        branch = inputs["branch"]
        default_branch = get_default_branch(token, repo)
        url = f"{GITHUB_API}/repos/{repo}/pulls"
        res = requests.post(
            url,
            json={"title": title, "body": body, "head": branch, "base": default_branch},
            headers=github_headers(token),
        )
        if res.status_code in (200, 201):
            return f"PR作成完了: {res.json()['html_url']}"
        return f"PR作成失敗: {res.status_code} {res.text}"

    return f"Unknown tool: {name}"


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
        _run_agent(token, repo, issue_number, issue_title, issue_body)
    finally:
        set_label(token, repo, issue_number, "in-progress", add=False)


def _run_agent(token: str, repo: str, issue_number: int, title: str, body: str) -> None:
    client = anthropic.Anthropic()

    system = (
        "あなたはGitHubリポジトリを自律的に管理するソフトウェアエンジニアです。\n"
        "与えられたIssueを分析し、コードを実装し、テストとlintを実行し、PRを作成してください。\n\n"
        "作業手順:\n"
        "1. list_files / read_file でリポジトリ構成と関連ファイルを把握する\n"
        "2. Issueの内容を理解して実装方針を決める\n"
        "3. run_bash で作業ブランチを作成する (例: git checkout -b fix/issue-{番号})\n"
        "4. write_file でコードを実装する\n"
        "5. run_bash でテストとlintを実行し、失敗したら修正して再実行する\n"
        "6. run_bash で git add / git commit / git push する\n"
        "7. create_pull_request でPRを作成する\n\n"
        "コミットメッセージ・PR本文はすべて日本語で記述してください。\n"
        f"gitのユーザー設定: git config user.email 'bot@monaca-mk2' && git config user.name 'monaca mk2'"
    )

    messages = [
        {
            "role": "user",
            "content": (
                f"以下のIssueを解決してください。\n\n"
                f"## Issue #{issue_number}: {title}\n\n{body}"
            ),
        }
    ]

    for iteration in range(MAX_AGENT_ITERATIONS):
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=TOOLS,
            messages=messages,
        )

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            print(f"エージェント完了 (イテレーション {iteration + 1})")
            return

        if response.stop_reason != "tool_use":
            print(f"予期しない停止理由: {response.stop_reason}")
            return

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            print(f"  ツール実行: {block.name}({json.dumps(block.input, ensure_ascii=False)[:120]})")
            result = execute_tool(block.name, block.input, token, repo)
            print(f"  結果: {result[:300]}")
            tool_results.append(
                {"type": "tool_result", "tool_use_id": block.id, "content": result}
            )

        messages.append({"role": "user", "content": tool_results})

    print(f"警告: 最大イテレーション ({MAX_AGENT_ITERATIONS}) に達しました。")


if __name__ == "__main__":
    _token = os.environ["GITHUB_TOKEN"]
    _repo = os.environ["GITHUB_REPOSITORY"]
    handle_oldest_issue(_token, _repo)
