# DDD-09: contract and test plan

Created: 2026-09-07T04:03:58+02:00. Status: TDD contract, before implementation.

Goal: turn the 13 described goal routes into independent executable process and task templates using the existing seven-family runner, not 13 engines.

DoD: every described node is mapped; short and normal feedback walkthroughs execute through WorkTools with real commands/Git/SQLite; task/sprint publication produces valid children rather than a prose-only success; templates and actual generated configs are validated by the existing goal owner. DDD boundaries and all previous tests remain checked. No live external integration is inferred from fixtures.

Scope: catalogue/typed task templates, full reference route definitions, planning publication through Task/Sprint, public-tool walkthroughs, documentation. Advanced project-specific check design and all detailed historical acceptance fixtures are not claimed executed.

TDD: new template tests currently fail at collection because the new API is absent. Publication and walkthrough tests precede implementation. Test fixtures make reviewer/user decisions explicit; they are not independent reviews. Full regression follows on frozen code, with separate test evidence.
