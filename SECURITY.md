# Security Policy

Languages: [English](SECURITY.md) | [Português](SECURITY.pt-BR.md) | [日本語](SECURITY.ja.md) | [Español](SECURITY.es.md)

MangaSensei is privacy-first software that processes user-provided manga pages, local OCR/model data and optional external AI requests. Security reports are welcome and should be handled privately.

## Supported Versions

Until the first public GitHub Release is published, security fixes target the current `main` branch. After stable releases begin, security fixes are applied to the latest supported release line, with patch releases used when appropriate.

The [GitHub Releases](https://github.com/Gyliardson/mangasensei/releases) page is the source of truth for published versions.

## Reporting a Vulnerability

**Do not open a public issue for security vulnerabilities.**

Use GitHub Private Vulnerability Reporting:

- [Report a vulnerability privately](https://github.com/Gyliardson/mangasensei/security/advisories/new)

Please include, when possible:

- The affected version, commit and platform.
- A minimal reproduction or precise description.
- The impact you observed or expect.
- Relevant logs with secrets, tokens, manga content and personal data removed.
- A suggested fix or mitigation, if you have one.

We aim to acknowledge a report within 5 business days. We will keep the reporter informed during investigation and coordinate disclosure before publishing details when a vulnerability is confirmed.

## Security Scope

Reports are especially useful for issues involving:

- Capability or authorization bypasses.
- Exposure of uploaded manga pages or retained user data.
- Upload validation, path traversal or unsafe file handling.
- Queue/worker isolation, job ownership or retention failures.
- Secret, credential or API-key exposure.
- Dependency, build, release or GitHub Actions supply-chain risks.
- Container privilege or filesystem isolation regressions.
- Unexpected external transmission of data, including Gemini integration behavior.

The following are generally **out of scope** unless MangaSensei's own integration creates the vulnerability:

- Secrets intentionally placed by a user in their own local `.env`.
- Vulnerabilities that exist only in an unsupported local modification.
- Third-party service or dependency vulnerabilities that cannot be mitigated in MangaSensei.
- Local OCR model weights or JMdict data merely existing on the user's own machine.

## Disclosure Process

1. A report is submitted privately.
2. A maintainer triages severity, reproducibility and affected boundaries.
3. A fix and regression coverage are prepared without publicly exposing exploit details.
4. Required CI/security gates are run on the exact fix SHA.
5. When needed, a security patch release is prepared.
6. The advisory is published with coordinated disclosure and reporter credit unless anonymity is requested.

## Safe Harbor

Good-faith security research and responsible disclosure are welcome. We will not pursue legal action against researchers who:

- Report through the private channel above.
- Avoid violating the privacy of others.
- Avoid destroying, corrupting or retaining data that is not theirs.
- Do not exploit a vulnerability beyond what is reasonably necessary to demonstrate it.
- Give maintainers reasonable time to investigate and remediate before public disclosure.

This safe-harbor statement applies to research conducted in good faith against MangaSensei itself; it does not authorize testing systems or data belonging to third parties.
