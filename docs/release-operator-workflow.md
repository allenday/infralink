# Operator Release Workflow

Issue: [#37](https://github.com/cyberstorm-dev/infralink/issues/37)

Registry YAML is the source of intent. Operators continue to edit profiles,
hosts, templates, image tags, and image SHAs directly. This CLI neither edits
that YAML nor makes generated state the source of truth.

## Command Tree

```text
infralink release inspect --release-validation PATH --admission PATH
infralink release validate-candidate --candidate PATH
infralink release render-publisher-request --candidate PATH --admission PATH
infralink release inspect-attestation --attestation PATH
```

Every command emits `infralink.cli/v1`: `ok`, `command.raw`,
`command.parsed`, either `result` or repair-oriented `error`, and no more than
three HATEOAS `next_actions`. Output is bounded to 64 artifacts/consumers and
does not include logs.

## Inputs And Handoffs

`infralink.release-candidate.v1` is generated from an ordinary YAML change by
CI. It binds an immutable release identity, exact registry/controller commits,
CI receipt, SHA-256 artifacts, and eligible consumers. It cannot contain a
branch or other mutable ref as authority.

`render-publisher-request` validates that candidate and the local admission
policy, then produces `infralink.publisher-request.v1`. The request binds the
same immutable release facts, channel, and sequence. It is an explicit input
to the trusted publisher, not an invocation of one.

`infralink.release-attestation.v1` is the publisher's immutable completion
record. Inspection reports its release binding and bounded consumer list. It
does not advertise an unimplemented host-shadow command; registry CI owns that
later workflow.

## Current Stubs

The CLI can validate/render/inspect locally today. It does not implement the
publisher. The publisher still requires the protected, least-privilege
Woodpecker/BWS/Gitea path tracked by infra-registry issue #251. Until that is
active, publisher eligibility is a clear no-go state; operators must not turn
the request object into raw signing or Git commands.

The producer contract has not yet selected canonical request bytes or a request
digest, so this slice does not invent an attestation-to-request digest binding.
That precise producer integration is tracked by Infralink issue #38.

## Examples

```sh
infralink release validate-candidate --candidate candidate.json
infralink release render-publisher-request --candidate candidate.json --admission admission.yml
infralink release inspect-attestation --attestation release-attestation.json
```
