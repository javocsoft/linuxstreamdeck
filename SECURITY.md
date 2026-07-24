# Security Policy

## Supported Versions

LinuxStreamDeck is an early-stage project. Only the latest published release is
supported with security fixes.

| Version | Supported |
| --- | --- |
| Latest published release | Yes |
| Older releases | No |

Users should reproduce a suspected vulnerability with the latest release when
it is safe to do so.

## Report a Vulnerability

Do not report a suspected vulnerability through a public GitHub Issue,
Discussion, pull request, or social media post.

Email **javocsoft@gmail.com** with the subject
`LinuxStreamDeck security report`.

Include the following information when it is available:

- The affected LinuxStreamDeck version or commit.
- The Linux distribution and desktop environment.
- The affected Stream Deck model, OBS version, or integration, when relevant.
- A clear description of the issue and its potential impact.
- Minimal steps or proof-of-concept material needed to reproduce it safely.
- Any mitigation or fix you have already identified.
- Whether you plan to disclose the issue elsewhere.

Do not include live API keys, OBS passwords, access tokens, private
configuration exports, or unrelated personal data. Replace sensitive values
with placeholders. If a large log or configuration sample is necessary, first
send a minimal report and agree on a safe way to share it.

The maintainer will review the report, may request additional information, and
will coordinate remediation and disclosure as appropriate. Please allow time
for a fix to be prepared and distributed before publishing technical details.

## Security Scope

Security reports may include, but are not limited to:

- Credential exposure or unsafe Secret Service handling.
- Unexpected command execution or unsafe action validation.
- Archive traversal or unsafe configuration import behavior.
- Unauthorized OBS websocket operations.
- Unsafe handling of local audio, images, paths, or device data.
- Dependency vulnerabilities with a demonstrated impact on LinuxStreamDeck.

General bugs, setup questions, and feature requests belong in GitHub Issues or
Discussions rather than the private security channel.
