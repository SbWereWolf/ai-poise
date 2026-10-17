-- Independent explicit-decision extension of the test-owned schema fixture.
CREATE TABLE requirement_confirmation_decisions (
    task_id TEXT NOT NULL,
    snapshot_revision INTEGER NOT NULL,
    sequence INTEGER NOT NULL,
    key TEXT NOT NULL,
    outcome_id TEXT,
    PRIMARY KEY (task_id, snapshot_revision, sequence),
    FOREIGN KEY (task_id, snapshot_revision) REFERENCES task_snapshots(task_id, snapshot_revision),
    FOREIGN KEY (outcome_id) REFERENCES requirement_outcomes(outcome_id)
);
