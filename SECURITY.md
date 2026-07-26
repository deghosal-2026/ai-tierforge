# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability, please report it privately. Do not open a public issue.

**Email:** security@ghosal.dev

Please include:
- A detailed description of the vulnerability
- Steps to reproduce
- The affected version(s)
- Any potential impact or exploit scenario

## Response Timeline

- **Acknowledgment:** Within 48 hours
- **Initial assessment:** Within 5 business days
- **Fix timeline:** Depends on severity — critical issues prioritized for immediate patch release

## Supported Versions

| Version | Supported |
|---|---|
| Latest (main) | ✅ |
| Older versions | ❌ |

## Security Design Principles

ai-tierforge is built with the following security principles:

- **All external calls are authenticated.** API tokens are scoped to read-only where possible.
- **No secrets persist.** Tokens are passed as environment variables or CLI args — never written to disk or logs.

## Common Security Knowledge

All contributors are expected to understand and avoid these common security pitfalls:

- **Injection attacks:** Never construct SQL, shell commands, or LDAP queries via string concatenation. Use parameterized queries and safe APIs.
- **Authentication bypass:** Never trust client-side identity assertions. All identity verification happens server-side.
- **Secrets management:** API keys, tokens, and credentials are loaded from environment variables. Never hard-code secrets.
- **Input validation:** All user-supplied input should be validated before reaching business logic. Expect and reject malformed input.

## What to Expect

If a vulnerability is confirmed:
1. A fix will be developed and tested
2. A security advisory will be published with the fix
3. Credit will be given to the reporter (unless anonymity is requested)