# Reference database

`harness-tasks.sqlite` is a validation-only Task DB generated from the same public seed API against temporary paths. It shows the expected single-project structure: `SPRINT-0001` with tasks `0001`, `0002`, `0003` and result dependencies `0001 → 0002 → 0003`.

Do not copy the reference DB into WSL production state. Generate the working DB after publishing the final project manifest so its task snapshots use the real project configuration.
