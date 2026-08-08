# MangaSensei へのコントリビューション

言語: [English](CONTRIBUTING.md) | [Português](CONTRIBUTING.pt-BR.md) | [日本語](CONTRIBUTING.ja.md) | [Español](CONTRIBUTING.es.md)

MangaSensei に関心を持っていただきありがとうございます。コントリビューション、issue、pull request は **English、Português、日本語、Español** のいずれでも歓迎します。参加することで、[行動規範](CODE_OF_CONDUCT.ja.md)に従うことに同意したものとみなします。

## 行動規範

[行動規範](CODE_OF_CONDUCT.ja.md)をお読みください。ハラスメントや排除的な行為は認められません。違反を見つけた場合は、文書に記載された非公開の報告方法を利用してください。

## セキュリティ

脆弱性を見つけた場合は、**公開 issue を作成しないでください**。[セキュリティポリシー](SECURITY.ja.md)の責任ある開示手順に従ってください。

## はじめに

1. [日本語 README](README.ja.md)を読みます。[English](README.md)、[Português](README.pt-BR.md)、[Español](README.es.md) も利用できます。
2. コードを変更する前に、関連するローカル quality gate を実行できることを確認してください。
3. 重複作業を避けるため、既存の issue、discussion、pull request を確認してください。
4. 大きな変更や非自明な変更では、先に discussion または issue で方針を相談してください。

## 開発フロー

保護された `main` と小さく焦点を絞った pull request を使います。

- 外部コントリビューターはリポジトリを fork してください。書き込み権限がある場合は目的別の branch を作成します。
- 変更は小さくレビューしやすく保ちます。巨大な PR 1 件より、焦点を絞った複数 PR を推奨します。
- 最終レビュー前に branch を最新の `main` に更新してください。
- `main` を対象に PR を開き、PR template を記入してください。
- 必須 CI check と未解決の review conversation が残っている状態では merge できません。

### Branch 名

短く内容が分かる名前を使ってください。

```text
feat/reading-order
fix/idempotency-conflict
docs/jmdict-license
```

### Conventional Commits

Commit message と PR title は [Conventional Commits] に従います。

```text
<type>(<scope>): <description>
```

例:

```text
feat(worker): persist dictionary version and digest
fix(cli): allow jmdict/models verify without database credentials
docs: add multilingual community guidance
test(runner): cover public error code mapping
```

主な type: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`, `build`, `style`, `revert`。

このリポジトリは squash merge を使用するため、レビュー済みの PR title が `main` 上の最終 commit title になります。

## 構成と規約

```text
backend/   Python API、worker、migrations、OCR、言語解析
frontend/  React SPA、reader components、Playwright tests
docs/      バージョン情報とビジュアル資料
tests/     Backend unit/integration tests
```

ガイドライン:

- Backend: module を小さく保ち、type hints、strict typing、設定済み coverage 要件を維持してください。
- Frontend: 既存の React/TypeScript パターンに従い、アクセシビリティを考慮してください。
- 生成データ、モデル weights、`.env`、認証情報、build artifacts を commit しないでください。
- Runtime artifact の場所を変更した場合は [`.gitignore`](.gitignore) と [`.dockerignore`](.dockerignore) を揃えてください。
- Local-first の設計とアップロード元画像の保持を守ってください。

## ローカル Quality Gates

Push 前に変更に関係する gate を実行してください。必須 CI の現在の定義は GitHub Actions workflow が source of truth です。

```powershell
# repository consistency
.\.venv\Scripts\python.exe scripts/version.py check
.\.venv\Scripts\python.exe scripts/check_markdown_links.py

# backend
.\.venv\Scripts\python.exe -m ruff check backend/src tests scripts
.\.venv\Scripts\python.exe -m mypy backend/src
$env:MANGASENSEI_TEST_DATABASE_URL='postgresql+psycopg://mangasensei:mangasensei@127.0.0.1:55432/mangasensei'
.\.venv\Scripts\python.exe -m pytest --cov

# frontend
npm run lint
npm run typecheck
npm run test:coverage
npm run build
npm run e2e
```

補足:

- Backend integration tests には PostgreSQL が必要です。ローカル開発 DB または Docker Compose の PostgreSQL service を利用してください。
- 任意の OCR smoke test は実モデルを読み込むため既定では skip されます。`MANGASENSEI_RUN_OCR_SMOKE=1` と `MANGASENSEI_MODEL_CACHE` を設定すると有効になります。
- Frontend E2E tests は Playwright web server を自動起動します。
- CI は Python distribution の build、clean wheel install、production Docker image build、security checks も実行します。

## ドキュメントと翻訳

英語ファイルは GitHub が認識する canonical integration files ですが、コントリビューター向け文書は英語、ブラジルポルトガル語、日本語、スペイン語で管理します。

README や共通ガイダンスを変更する場合:

- 可能な限り同じ PR で影響する言語版も更新してください。
- Command、path、version、technical contract は翻訳間で同一にしてください。
- `LICENSE`、`CHANGELOG.md`、`THIRD_PARTY_NOTICES.md`、manifest、workflow の並行翻訳を source of truth として作らないでください。
- 読者向けファイル参照には Markdown link を使用してください。

## バージョンとリリース

プロジェクトは [Semantic Versioning](https://semver.org/) を使用し、[changelog](CHANGELOG.md) に人がレビューする release notes を保持します。[`pyproject.toml`](pyproject.toml) の Python project version が基準で、repository tooling が必要な mirror を同期します。

Version mirror を手作業で個別編集しないでください。[`scripts/version.py`](scripts/version.py) が現在の tooling である場合は次を使います。

```powershell
.\.venv\Scripts\python.exe scripts/version.py set 0.2.0
```

この command は機械的な version mirror を更新しますが release notes は書きません。`[Unreleased]` の内容は [`CHANGELOG.md`](CHANGELOG.md) で手動で整理してください。

Release commit が CI を通過して `main` に merge された後、対応する `vX.Y.Z` tag が [release workflow](.github/workflows/release.yml) を起動します。Release の時期は maintainer が決定します。通常の PR で release tag を作成しないでください。

## Issue の報告

可能な場合は issue template を使用してください。4 つの対応言語のどれでも記述できます。

- **Bug report**: version または commit、OS、再現手順、関連 log を含めてください。
- **Feature request**: 目的、期待する動作、privacy/API/storage への影響を説明してください。

## Pull Requests

PR template を記入してください。良い PR は次を満たします。

- 何を、なぜ変更するかを説明する。
- 関連 issue があれば `Closes #123` などで参照する。
- 実行した test/check を記載する。
- 必須 quality gate を通過する。
- 影響する documentation を更新する。
- 無関係な cleanup を含めない。

Maintainer は機能、セキュリティ、互換性、プロジェクト方針への適合を確認します。変更依頼、大きな PR の分割、またはスコープ外提案の理由付き close を行う場合があります。

## ライセンス

コントリビューションはプロジェクトと同じ `GPL-3.0-only` 条件でライセンスされることに同意したものとみなします。[LICENSE](LICENSE) を参照してください。

[Conventional Commits]: https://www.conventionalcommits.org/en/v1.0.0/
