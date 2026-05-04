#!/usr/bin/env python3
"""PR review handler: validates review comments and suggests fixes using Claude."""

import base64
import json
import os
import re
import sys
from typing import Optional

import anthropic
import requests

GITHUB_API = "https://api.github.com"
MAX_FILE_BYTES = 50_000  # 50 KB cap to keep prompts reasonable


def github_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def get_file_content(token: str, repo: str, path: str, ref: str) -> Optional[str]:
    url = f"{GITHUB_API}/repos/{repo}/contents/{path}"
    res = requests.get(url, params={"ref": ref}, headers=github_headers(token))
    if res.status_code != 200:
        return None
    raw = base64.b64decode(res.json()["content"]).decode("utf-8", errors="replace")
    if len(raw.encode()) > MAX_FILE_BYTES:
        raw = raw[: MAX_FILE_BYTES] + "\n... (truncated)"
    return raw


def post_reply(token: str, repo: str, pr_number: int, comment_id: int, body: str) -> None:
    url = f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}/comments/{comment_id}/replies"
    requests.post(url, json={"body": body}, headers=github_headers(token)).raise_for_status()


def post_issue_comment(token: str, repo: str, pr_number: int, body: str) -> None:
    url = f"{GITHUB_API}/repos/{repo}/issues/{pr_number}/comments"
    requests.post(url, json={"body": body}, headers=github_headers(token)).raise_for_status()


def extract_json(text: str) -> dict:
    # Strip markdown code fences if present
    m = re.search(r"```(?:json)?\s*([\s\S]+?)```", text)
    raw = m.group(1) if m else text
    return json.loads(raw.strip())


def validate_comment(
    client: anthropic.Anthropic,
    comment_body: str,
    file_path: str,
    diff_hunk: str,
) -> Optional[dict]:
    """Ask Claude whether the review comment is a legitimate concern."""
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        system=[
            {
                "type": "text",
                "text": "You are a senior software engineer performing code review triage. Reply in JSON only.",
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": (
                    f"File: {file_path}\n\n"
                    f"Diff hunk:\n```\n{diff_hunk}\n```\n\n"
                    f"Review comment:\n{comment_body}\n\n"
                    "Evaluate this review comment. Return JSON:\n"
                    '{"validity":"valid|questionable|invalid",'
                    '"severity":"critical|major|minor|nitpick",'
                    '"explanation":"one sentence",'
                    '"should_fix":true|false}'
                ),
            }
        ],
    )
    try:
        return extract_json(response.content[0].text)
    except (json.JSONDecodeError, IndexError):
        print("Failed to parse validation JSON, skipping comment")
        return None


def generate_fix(
    client: anthropic.Anthropic,
    comment_body: str,
    file_path: str,
    diff_hunk: str,
    file_content: Optional[str],
) -> str:
    """Ask Claude to produce a concrete fix for a valid review comment."""
    content_blocks: list[dict] = [
        {
            "type": "text",
            "text": f"You are a senior software engineer. Generate a concise, correct fix.",
        }
    ]

    if file_content:
        content_blocks.append(
            {
                "type": "text",
                "text": f"File: {file_path}\n```\n{file_content}\n```",
                "cache_control": {"type": "ephemeral"},  # cache large file content
            }
        )

    content_blocks.append(
        {
            "type": "text",
            "text": (
                f"Diff context:\n```\n{diff_hunk}\n```\n\n"
                f"Review comment: {comment_body}\n\n"
                "Provide the fix as a code block followed by a one-line explanation."
            ),
        }
    )

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[{"role": "user", "content": content_blocks}],
    )
    return response.content[0].text


SEVERITY_EMOJI = {
    "critical": "🔴",
    "major": "🟠",
    "minor": "🟡",
    "nitpick": "⚪",
}


def handle_line_comment(event: dict) -> None:
    comment = event["comment"]
    pr = event["pull_request"]

    # Avoid infinite loops: skip bots and replies
    if comment.get("user", {}).get("type") == "Bot":
        print("Skipping bot comment")
        return
    if comment.get("in_reply_to_id"):
        print("Skipping reply comment")
        return

    token = os.environ["GITHUB_TOKEN"]
    repo = os.environ["GITHUB_REPOSITORY"]
    comment_body: str = comment["body"]
    file_path: str = comment["path"]
    diff_hunk: str = comment["diff_hunk"]
    pr_number: int = pr["number"]
    comment_id: int = comment["id"]
    head_sha: str = pr["head"]["sha"]

    file_content = get_file_content(token, repo, file_path, head_sha)
    client = anthropic.Anthropic()

    print(f"Validating comment on {file_path} ...")
    validation = validate_comment(client, comment_body, file_path, diff_hunk)
    if validation is None:
        print("Skipping comment: validation result unavailable")
        return
    print(f"Validation: {json.dumps(validation, ensure_ascii=False)}")

    emoji = SEVERITY_EMOJI.get(validation.get("severity", "minor"), "🟡")
    validity = validation.get("validity", "questionable")
    explanation = validation.get("explanation", "")

    if validity == "invalid":
        reply = f"**[AI Review]** {emoji} このレビュー指摘は妥当ではない可能性があります。\n\n> {explanation}"
    elif validity == "questionable":
        reply = f"**[AI Review]** {emoji} このレビュー指摘は状況依存です。\n\n> {explanation}"
    else:
        if validation.get("should_fix", True):
            print("Generating fix suggestion ...")
            fix = generate_fix(client, comment_body, file_path, diff_hunk, file_content)
            reply = f"**[AI Review]** {emoji} 妥当な指摘です。修正案:\n\n{fix}"
        else:
            reply = f"**[AI Review]** {emoji} 妥当な指摘です（コード変更不要）。\n\n> {explanation}"

    post_reply(token, repo, pr_number, comment_id, reply)
    print("Reply posted.")


def handle_review(event: dict) -> None:
    review = event["review"]
    pr = event["pull_request"]

    if review.get("state") != "CHANGES_REQUESTED":
        print(f"Skipping review state: {review.get('state')}")
        return

    review_body = (review.get("body") or "").strip()
    if not review_body:
        print("Review has no body, skipping")
        return

    if review.get("user", {}).get("type") == "Bot":
        print("Skipping bot review")
        return

    token = os.environ["GITHUB_TOKEN"]
    repo = os.environ["GITHUB_REPOSITORY"]
    pr_number: int = pr["number"]
    client = anthropic.Anthropic()

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system="You are a senior software engineer. Summarize PR review action items clearly and concisely in Japanese.",
        messages=[
            {
                "role": "user",
                "content": (
                    f"以下のPRレビュー（CHANGES_REQUESTED）の対応事項を箇条書きでまとめてください。\n\n{review_body}"
                ),
            }
        ],
    )

    summary = response.content[0].text
    body = f"**[AI Review Summary]** 変更要求の対応事項:\n\n{summary}"
    post_issue_comment(token, repo, pr_number, body)
    print("Summary comment posted.")


def main() -> None:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    event_name = os.environ.get("GITHUB_EVENT_NAME")

    if not event_path:
        print("GITHUB_EVENT_PATH not set", file=sys.stderr)
        sys.exit(1)

    with open(event_path) as f:
        event = json.load(f)

    print(f"Event: {event_name}")

    if event_name == "pull_request_review_comment":
        handle_line_comment(event)
    elif event_name == "pull_request_review":
        handle_review(event)
    else:
        print(f"Unsupported event: {event_name}")


if __name__ == "__main__":
    main()
