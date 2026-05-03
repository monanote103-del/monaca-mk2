# monaca-mk2

PRレビュー指摘を検知して妥当性確認と修正提案を行うGitHub Actions。

## 動作フロー

```
PRレビューコメント投稿
        ↓
GitHub Actions 起動
        ↓
Claude で妥当性判定（valid / questionable / invalid）
        ↓
valid → Claude で修正案を生成
        ↓
元コメントにリプライとして投稿
```

## セットアップ

### 1. Secrets の設定

GitHubリポジトリの **Settings → Secrets and variables → Actions** で追加:

| Secret | 内容 |
|--------|------|
| `ANTHROPIC_API_KEY` | Anthropic APIキー |

`GITHUB_TOKEN` はActions が自動提供するため不要。

### 2. Permissions の確認

ワークフローファイルに `pull-requests: write` と `contents: read` が設定済み。  
リポジトリの **Settings → Actions → General → Workflow permissions** で  
「Read and write permissions」を有効にすること。

## トリガー

| イベント | 条件 | 処理 |
|----------|------|------|
| `pull_request_review_comment` (created) | ボットおよびリプライを除く | 妥当性判定 → 修正案リプライ |
| `pull_request_review` (submitted) | `CHANGES_REQUESTED` かつ本文あり | 対応事項サマリをコメント投稿 |

## 出力例

**行コメントへのリプライ:**
```
[AI Review] 🟡 妥当な指摘です。修正案:

```python
# 修正後コード
```
一行説明
```

**CHANGES_REQUESTED サマリ:**
```
[AI Review Summary] 変更要求の対応事項:

- ...
- ...
```
