# Security Policy

MangaSensei takes security seriously. This document explains how to report
vulnerabilities and what to expect.

## Supported Versions

Security fixes are applied to the latest release. Only the newest patch version
of each release line receives security updates.

| Version | Supported |
| --- | --- |
| 0.1.x | Yes (current) |

## Reporting a Vulnerability

Do **not** open a public issue for security problems. Please report privately.

- Open a private security advisory on GitHub:
  `https://github.com/gyliardson/mangasensei/security/advisories/new`
- Or email the maintainers at the address listed in the repository description.

Please include, when possible:

- The affected version and platform.
- A minimal reproduction or description of the issue.
- The impact you observed or expect.
- Any suggested fix, if you have one.

You should receive an acknowledgment within 5 business days. We will keep you
informed of the investigation and, once a fix is ready, coordinate disclosure
with you before it is public.

## Scope

We care about vulnerabilities in the application code, its build, and its
default deployment. The following are **out of scope**:

- Secrets you intentionally commit to your own `.env`.
- Local model weights and JMdict data (they are personal local artifacts).
- Third-party dependencies — report those to their respective projects, unless
  a fix belongs in MangaSensei's integration code.

## Process

1. An issue is reported privately.
2. A maintainer triages and confirms the vulnerability.
3. A fix is developed and reviewed.
4. Depending on severity, a patch release is published and the advisory is
   released publicly with credit to the reporter (unless anonymity is requested).

## Safe Harbor

Good-faith research and responsible disclosure are welcome. We will not pursue
legal action against researchers who:

- Report vulnerabilities through the private channels above,
- Avoid violating the privacy of others and avoid destroying or corrupting data,
- Do not exploit a vulnerability beyond what is needed to demonstrate it.
