# Vitest API validation boundary

These references contain examples for multiple Vitest generations. Select the installed version and relevant operation before use; do not interpret v4/v5 notes as a universal configuration contract or a current release announcement. Target versions/runtimes/commands are read from accepted project facts and resolved through Poise-owned configuration.

The migration fixture uses the supplied archived Vitest 4.1.9 and its actual Vite/Node dependencies, not a new `latest` installation. It exercises explicit configuration, exact primitive/JSON assertions, fake timers and cleanup, Vue reactivity/configuration and glob exclusion behavior. An isolated negative probe checks that exclusive tests fail with `allowOnly: false`; this is a guard test, not a permission to relax the setting. Logs identify the exact scope and package versions.

No full ERP suite, browser/SSR/coverage provider, every reference example, Vitest v5, or target deployment is claimed. Broad commands, interactive snapshot update keys and `allowOnly: true` samples are reference-only: they never authorize weakening verification. Resolve unresolved configuration/version facts before applying a snippet. Primary sources: [Vitest configuration](https://vitest.dev/config/), [Vitest filtering](https://vitest.dev/guide/filtering), [snapshot guide](https://vitest.dev/guide/snapshot).
