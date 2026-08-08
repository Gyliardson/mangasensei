# セキュリティポリシー

言語: [English](SECURITY.md) | [Português](SECURITY.pt-BR.md) | [日本語](SECURITY.ja.md) | [Español](SECURITY.es.md)

MangaSensei は、ユーザーが提供する漫画ページ、ローカル OCR/モデルデータ、任意の外部 AI リクエストを扱う privacy-first ソフトウェアです。セキュリティ報告は歓迎しますが、非公開で取り扱ってください。

## サポート対象バージョン

最初の公開 GitHub Release が公開されるまでは、セキュリティ修正は現在の `main` branch を対象とします。安定版 release の公開開始後は、最新のサポート対象 release line に修正を適用し、必要に応じて patch release を行います。

公開済みバージョンの source of truth は [GitHub Releases](https://github.com/Gyliardson/mangasensei/releases) です。

## 脆弱性の報告

**セキュリティ脆弱性について公開 issue を作成しないでください。**

GitHub Private Vulnerability Reporting を利用してください。

- [脆弱性を非公開で報告する](https://github.com/Gyliardson/mangasensei/security/advisories/new)

可能であれば以下を含めてください。

- 影響を受ける version、commit、platform。
- 最小の再現手順または正確な説明。
- 観測した、または想定される影響。
- Secret、token、漫画コンテンツ、個人データを除いた関連 log。
- 修正案や mitigation があればその内容。

原則として 5 営業日以内の受領確認を目指します。調査中は報告者へ進捗を共有し、脆弱性が確認された場合は詳細公開前に disclosure を調整します。

## セキュリティ範囲

特に次のような問題を歓迎します。

- Capability または authorization bypass。
- アップロードされた漫画ページや保持データの露出。
- Upload validation、path traversal、安全でない file handling。
- Queue/worker isolation、job ownership、retention の不具合。
- Secret、credential、API key の露出。
- Dependency、build、release、GitHub Actions の supply-chain リスク。
- Container privilege や filesystem isolation の regression。
- Gemini integration を含む、意図しない外部データ送信。

MangaSensei 自身の integration が脆弱性を作っている場合を除き、次は通常 **対象外** です。

- ユーザーが自身のローカル `.env` に意図的に保存した secret。
- サポート外のローカル改変だけで発生する問題。
- MangaSensei 側で mitigation できない第三者 service/dependency の脆弱性。
- ローカル OCR model weights や JMdict data がユーザー自身の環境に存在すること自体。

## Disclosure Process

1. 報告を非公開で受け取ります。
2. Maintainer が severity、再現性、影響 boundary を評価します。
3. Exploit details を公開せずに修正と regression coverage を準備します。
4. 修正 SHA そのものに対して必須 CI/security gate を実行します。
5. 必要に応じて security patch release を準備します。
6. 匿名希望がない限り報告者に credit を付け、調整した時期に advisory を公開します。

## Safe Harbor

善意のセキュリティ研究と責任ある開示を歓迎します。次を守る研究者に対して法的措置を追求しません。

- 上記の非公開チャネルから報告する。
- 他者のプライバシーを侵害しない。
- 自分のものではないデータを破壊、改変、保持しない。
- 実証に合理的に必要な範囲を超えて脆弱性を悪用しない。
- 公開前に maintainer が調査と修正を行う合理的な時間を与える。

この safe-harbor statement は MangaSensei 自体に対する善意の研究に適用され、第三者の system や data をテストする権限を与えるものではありません。
