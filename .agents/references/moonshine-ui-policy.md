# MoonShine UI policy for the selected project

Apply only where the target actually uses MoonShine and only against its installed version. Prefer native pages/resources/fields/actions/layouts and then documented extension points before custom UI. Resolve actual UI ownership, styling, supported browsers and custom mount contracts from target-project facts and Poise-owned configuration; do not copy ERP-specific editor/viewer allowlists.

For normal native components no catalogue, exception registry or ADR ceremony is required. For custom divergence record the material unmet requirement, native/extension options inspected, concrete limitation, chosen boundary and focused verification in existing Task content. Convenience or existing custom code alone is not proof that native facilities cannot meet the requirement. Existing unrelated UI is not automatically in scope for replacement.

Keep reusable business behavior at its domain/application owner and authorization at the authoritative server/application boundary. Respect the actual project contract for host navigation/actions and embedded UI; do not invent blanket host-wrapper or Vue restrictions. Use the [native standard](moonshine-native-gui-standard.md) for relevant composition/a11y checks and the project’s existing migration/test owners for those independent policies.
