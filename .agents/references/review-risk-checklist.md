# Review risk checklist

Select only boundaries affected by the actual requirements and diff. Read project-specific facts from the explicitly selected checkout path and resolve AI-poise-owned commands, versions, browsers and routing from AI poise configuration. This is not a mandatory full-system audit.

## Obligations and evidence

Check requirement/acceptance drift, assumptions presented as observations, stale evidence, wrong source/build identity, premature success claims, missing documentation and artifacts due at an earlier gate. Trace findings to the confirmed obligation, responsible producer stage and later check. Saving and structural validation do not establish substantive correctness.

## Contracts and data

Trace producer/consumer payloads in both directions. Review ownership, normalization, authorization, transaction and irreversible-effect boundaries. Inspect applicable failure, retry, concurrency and recovery cases. Do not add a compatibility branch or a fallback that hides the original failure. Public serialization must follow the declared contract and exclude unrelated/sensitive state; no serializer implementation is mandated by this checklist.

## Tests

Reject production-derived expected values, silent fixture regeneration, widened tested-class allowlists, missing meaningful negative cases, and assertions that observe only a mock instead of the required boundary. Canonical JSON longer than 99 Unicode characters belongs in a file, with exactly 99 eligible inline. For a non-ORM subject, use parameterized SQL for setup/effect inspection; enumerate necessary fields, never a wildcard projection. Historical migrations cannot import production models/enums/services or hide dependencies in strings.

## Applicable interface and infrastructure risks

Where the project actually uses them, check frontend ownership, single-source state, navigation/error targets, native component reuse, accessibility and required visual states. Browser and host-publication obligations come from the project contract, not an inherited ERP topology. Check loaded build identity for a browser defect. Do not infer deployment permission from a build or smoke pass.

## Review integrity

Use only targeted checks plus maintained fast smoke during ordinary Task completion/integration. Full suites belong to an explicitly assigned release scenario. Never invent findings, sessions, skipped-role evidence or a successful retry. Every pending resolution needs a reasoned decision; a no-finding result names its scope and limits. Self-review does not replace the independent reviewer, and inspection does not grant acceptance/publication authority.
