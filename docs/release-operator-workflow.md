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
CI. Its packaged structural JSON Schema is
`infralink/schemas/release/v1/release-candidate.v1.schema.json`; a sanitized
example is `examples/release/release-candidate.v1.json`. It binds a release
identity and its explicit channel/sequence, exact registry/controller commits,
a CI receipt, SHA-256 artifact digests, and a bounded, unique consumer list.
The typed `infralink.release.contracts.ReleaseCandidateV1` model is the
normative semantic validator used by the CLI and registry CI; it additionally
checks identity/channel/sequence coherence, safe unique artifact paths, and
unique consumers. Both layers reject unknown fields, including branch/ref
fields: mutable refs cannot be release authority.

`render-publisher-request` validates that candidate and the local admission
policy, then produces `infralink.publisher-request.v1`. The request binds the
same immutable release facts, channel, and sequence. It is an explicit input
to the trusted publisher, not an invocation of one.

`infralink.release-attestation.v1` is the publisher's immutable completion
record. Its packaged structural JSON Schema is
`infralink/schemas/release/v1/release-attestation.v1.schema.json`; a sanitized
example is `examples/release/release-attestation.v1.json`. It repeats every
candidate evidence binding, adds a bounded publisher CI receipt, and binds the
created tag name to the release identity plus the immutable annotated tag
object SHA-1. The typed `ReleaseAttestationV1` model additionally verifies the
tag/release binding. Inspection reports the full evidence binding and bounded
consumer list. It does not advertise an unimplemented host-shadow command;
registry CI owns that later workflow.

The CLI retains reader compatibility for the flat `v1` shapes emitted by the
initial #37 release CLI. Those older attestations contain no publisher
repository, tag object, candidate receipt, or artifact evidence; inspection
represents each as absent rather than inventing provenance. New registry CI
must emit the public nested contract above.

## Current Stubs

The CLI can validate/render/inspect locally today. It does not implement the
publisher. The publisher still requires the protected, least-privilege
Woodpecker/BWS/Gitea path tracked by infra-registry issue #251. Until that is
active, publisher eligibility is a clear no-go state; operators must not turn
the request object into raw signing or Git commands.

The producer contract deliberately does not select canonical publisher-request
bytes or a request digest. Registry #251 must add that implementation-specific
publisher evidence without weakening these release facts or creating a second
signing path.

## Examples

```sh
infralink release validate-candidate --candidate candidate.json
infralink release render-publisher-request --candidate candidate.json --admission admission.yml
infralink release inspect-attestation --attestation release-attestation.json
```
