---
name: Review response scope
description: PR review responses should include review summary comments and REQUEST_CHANGES, not just inline comments
type: feedback
---

PRレビューへの対応時は、個別のコメントだけでなく、review summary（レビュー全体のサマリーコメント）および REQUEST_CHANGES ステータスのレビューも対応対象に含める。

**Why:** ユーザーがコミットメッセージ経由でこのルールを明示した。

**How to apply:** `gh pr review` や `gh api` でレビューを取得する際、inline commentsだけでなく、review bodyおよびstate=REQUEST_CHANGESのレビューも確認し、それらへの対応も行う。
