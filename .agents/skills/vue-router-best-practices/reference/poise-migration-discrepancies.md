# Recorded source discrepancies — not permission to rewrite policy

C072 explicitly preserves the prohibition on `next()` and withdraws changes to guard ordering and other disputed rules. The eight imported failure-mode references therefore retain their source bytes; they are not blanket approval of every snippet. Current Task policy overrides legacy sample use of `next()`. Record a separate owner decision before changing disputed examples.

Observed during migration:

- The source calls `next()` deprecated and includes legacy `next()` and `beforeRouteEnter(next(vm))` examples. The primary [navigation-guard documentation](https://router.vuejs.org/guide/advanced/navigation-guards.html) still describes it as supported. This API fact is recorded, **not** used to soften the skill's ban.
- A source async-guard example marked “forgot return” actually contains an awaited check and a return redirect. That label is not evidence of a reproduced failure.
- The source timeout example allows navigation after an authentication-check timeout, and another network-error example returns true. These are unverified reference scenarios, not authorization to bypass the target's access boundary. Resolve the target's explicit security contract before use; no authentication bypass was implemented or tested here.
- The source's `return Error` explanation differs from the documented throw-Error path. The source guard-order material also remains unchanged as directed. No correctness claim about those disputed statements is made.

The operator's focused checks cover exact installed package behavior and the adapted Pinia URL example, not every preserved legacy router scenario, target host, browser, access policy or route component. Required automatic Poise skill routing remains a separate unmet owner dependency under the saved write scope.
