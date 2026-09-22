# Security policy

jev-guard is a security layer, so a bug in it can let an attack through. Please report
vulnerabilities privately.

## Reporting

Use GitHub's private reporting: **Security → Report a vulnerability** on
[rudra72r/jev-guard](https://github.com/rudra72r/jev-guard/security). Don't open a public
issue for a vulnerability.

Please include:

- the jev-guard version and policy
- a minimal input that shows the problem (use synthetic data, never real personal data)
- what you expected and what happened

You'll get an acknowledgement within 3 days and a plan within 10. Fixed issues are credited
in the release notes unless you'd rather stay anonymous.

## What counts as a vulnerability

- A way to make a check **allow** content that the policy's own rules should block: a
  parsing bug, an aggregation bug, a streaming check that can be skipped, or an unchecked
  path in an integration.
- Anything that leaks data jev-guard handles: API keys in logs or errors, raw values
  from `redact()` appearing in `found` or in what's sent to Jev, report files exposing data.
- Code execution through policy files or reports (policy YAML is loaded with `safe_load`
  only, and HTML reports escape all log text).

## What doesn't count (but please still report it as a normal issue)

- **A prompt that fools Jev.** jev-guard is probabilistic, and some attacks will get past
  any threshold. Use the "Missed attack / false alarm" issue template. These reports
  improve the policies and the eval set, and they're very welcome.
- Attacks in languages other than English (v0.1 is English-only).

## Supported versions

Security fixes go into the latest release.
