CREATE TABLE requirements_meta (
    revision INTEGER NOT NULL,
    schema_version INTEGER NOT NULL
);
CREATE TABLE requirements (
    id TEXT PRIMARY KEY,
    level TEXT NOT NULL,
    current_revision INTEGER NOT NULL
);
CREATE TABLE requirement_revisions (
    id TEXT NOT NULL REFERENCES requirements(id),
    revision INTEGER NOT NULL,
    status TEXT NOT NULL,
    text TEXT NOT NULL,
    owner_revision INTEGER NOT NULL,
    origin TEXT NOT NULL,
    PRIMARY KEY (id, revision)
);
CREATE TABLE application_purposes (
    application_id TEXT PRIMARY KEY REFERENCES requirements(id),
    purpose TEXT NOT NULL,
    current_revision INTEGER NOT NULL
);
CREATE TABLE application_purpose_revisions (
    application_id TEXT NOT NULL REFERENCES application_purposes(application_id),
    revision INTEGER NOT NULL,
    rationale TEXT NOT NULL,
    origin TEXT NOT NULL,
    PRIMARY KEY (application_id, revision)
);
CREATE TABLE system_application_versions (
    system_id TEXT NOT NULL REFERENCES requirements(id),
    application_id TEXT NOT NULL REFERENCES requirements(id),
    valid_from INTEGER NOT NULL,
    valid_to INTEGER,
    PRIMARY KEY (system_id, application_id, valid_from)
);
CREATE TABLE task_identities (
    task_id TEXT PRIMARY KEY,
    origin TEXT NOT NULL,
    current_statement_revision INTEGER NOT NULL
);
CREATE TABLE task_creation_requests (
    creation_request_id TEXT PRIMARY KEY,
    intent TEXT NOT NULL,
    task_id TEXT NOT NULL REFERENCES task_identities(task_id),
    response TEXT NOT NULL
);
CREATE TABLE task_statements (
    task_id TEXT NOT NULL REFERENCES task_identities(task_id),
    statement_revision INTEGER NOT NULL,
    goal TEXT NOT NULL,
    definition_of_done TEXT NOT NULL,
    origin TEXT NOT NULL,
    PRIMARY KEY (task_id, statement_revision)
);
CREATE TABLE task_requirement_keys (
    task_id TEXT NOT NULL REFERENCES task_identities(task_id),
    key TEXT NOT NULL,
    PRIMARY KEY (task_id, key)
);
CREATE TABLE task_requirement_versions (
    task_id TEXT NOT NULL,
    statement_revision INTEGER NOT NULL,
    key TEXT NOT NULL,
    position INTEGER NOT NULL,
    status TEXT NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY (task_id, statement_revision, key),
    FOREIGN KEY (task_id, statement_revision) REFERENCES task_statements(task_id, statement_revision),
    FOREIGN KEY (task_id, key) REFERENCES task_requirement_keys(task_id, key)
);
CREATE TABLE application_task_links (
    task_id TEXT NOT NULL,
    statement_revision INTEGER NOT NULL,
    key TEXT NOT NULL,
    application_id TEXT NOT NULL REFERENCES requirements(id),
    PRIMARY KEY (task_id, statement_revision, key, application_id),
    FOREIGN KEY (task_id, statement_revision, key) REFERENCES task_requirement_versions(task_id, statement_revision, key)
);
CREATE TABLE task_snapshots (
    task_id TEXT NOT NULL,
    snapshot_revision INTEGER NOT NULL,
    statement_revision INTEGER NOT NULL,
    registry_revision INTEGER NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (task_id, snapshot_revision),
    UNIQUE (task_id, statement_revision),
    FOREIGN KEY (task_id, statement_revision) REFERENCES task_statements(task_id, statement_revision)
);
CREATE TABLE requirement_outcomes (
    outcome_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    snapshot_revision INTEGER NOT NULL,
    kind TEXT NOT NULL,
    keys TEXT NOT NULL,
    FOREIGN KEY (task_id, snapshot_revision) REFERENCES task_snapshots(task_id, snapshot_revision)
);
CREATE TABLE requirement_requests (
    protocol_namespace TEXT NOT NULL,
    request_id TEXT NOT NULL,
    intent TEXT NOT NULL,
    response TEXT NOT NULL,
    PRIMARY KEY (protocol_namespace, request_id)
);
CREATE TABLE domain_areas (
    id TEXT PRIMARY KEY,
    business_name TEXT NOT NULL,
    definition TEXT NOT NULL
);
CREATE TABLE domain_entities (
    id TEXT PRIMARY KEY,
    domain_id TEXT NOT NULL REFERENCES domain_areas(id),
    business_name TEXT NOT NULL,
    code_name TEXT NOT NULL,
    definition TEXT NOT NULL,
    UNIQUE (domain_id, id)
);
CREATE TABLE domain_actions (
    id TEXT PRIMARY KEY,
    domain_id TEXT NOT NULL REFERENCES domain_areas(id),
    business_name TEXT NOT NULL,
    code_name TEXT NOT NULL,
    definition TEXT NOT NULL,
    UNIQUE (domain_id, id)
);
CREATE TABLE requirement_entities (
    requirement_id TEXT NOT NULL REFERENCES requirements(id),
    domain_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    PRIMARY KEY (requirement_id, domain_id, entity_id),
    FOREIGN KEY (domain_id, entity_id) REFERENCES domain_entities(domain_id, id)
);
CREATE TABLE requirement_actions (
    requirement_id TEXT NOT NULL REFERENCES requirements(id),
    domain_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    PRIMARY KEY (requirement_id, domain_id, action_id),
    FOREIGN KEY (domain_id, action_id) REFERENCES domain_actions(domain_id, id)
);
