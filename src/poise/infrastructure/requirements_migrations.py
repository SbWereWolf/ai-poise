"""Historical Requirements schema 1 → 2; caller owns the SQL transaction."""


def validate_schema(connection, version):
    """Inspect literal supported structures; extra unrelated objects are allowed."""
    definitions = _schema_2_definitions() if version == 2 else {}
    columns = {
        "requirements_meta": [("revision", "INTEGER", 1, 0)],
        "requirements": [("id", "TEXT", 0, 1), ("level", "TEXT", 1, 0),
                         ("status", "TEXT", 1, 0), ("text", "TEXT", 1, 0)],
        "requirement_links": [("system_id", "TEXT", 1, 1), ("application_id", "TEXT", 1, 2)],
        "requirement_requests": [("request_id", "TEXT", 0, 1),
                                 ("digest", "TEXT", 1, 0), ("response", "TEXT", 1, 0)],
    }
    unique = {"requirements": {("id",)}, "requirement_links": {("system_id", "application_id")},
              "requirement_requests": {("request_id",)}}
    foreign = {"requirement_links": {("requirements", ("system_id",), ("id",)),
                                     ("requirements", ("application_id",), ("id",))}}
    if version == 2:
        columns["domain_areas"] = [("id", "TEXT", 1, 1), ("business_name", "TEXT", 1, 0),
                                   ("definition", "TEXT", 1, 0)]
        unique["domain_areas"] = {("id",), ("business_name",)}
        for table in ("domain_entities", "domain_actions"):
            columns[table] = [("id", "TEXT", 1, 1), ("domain_id", "TEXT", 1, 0),
                              ("business_name", "TEXT", 1, 0), ("code_name", "TEXT", 1, 0),
                              ("definition", "TEXT", 1, 0)]
            unique[table] = {("id",), ("domain_id", "id"),
                             ("domain_id", "business_name"), ("domain_id", "code_name")}
            foreign[table] = {("domain_areas", ("domain_id",), ("id",))}
        for table, name, parent in (("requirement_entities", "entity_id", "domain_entities"),
                                     ("requirement_actions", "action_id", "domain_actions")):
            columns[table] = [("requirement_id", "TEXT", 1, 1), ("domain_id", "TEXT", 1, 2),
                              (name, "TEXT", 1, 3)]
            unique[table] = {("requirement_id", "domain_id", name)}
            foreign[table] = {("requirements", ("requirement_id",), ("id",)),
                              (parent, ("domain_id", name), ("domain_id", "id"))}
    for table, expected in columns.items():
        kind = connection.execute("SELECT type,sql FROM sqlite_master WHERE name=?", (table,)).fetchone()
        actual = [(row[1], row[2], row[3], row[5])
                  for row in connection.execute(f"PRAGMA table_info('{table}')")]
        if kind is None or kind[0] != "table" or actual != expected:
            raise ValueError(f"Requirements DB structure does not match schema {version}: {table}")
        if table in definitions and _sql_identity(kind[1]) != _sql_identity(definitions[table]):
            raise ValueError(f"Requirements DB structure has unsupported definition: {table}")
        indexes = set()
        for row in connection.execute(f"PRAGMA index_list('{table}')"):
            if row[2]:
                # Index names originate in SQLite, not the request. Quote them
                # as string literals for this historical inspection primitive.
                index = row[1].replace("'", "''")
                fields = [item for item in connection.execute(f"PRAGMA index_xinfo('{index}')") if item[5]]
                signature = tuple(item[2] for item in fields)
                if (row[4] or signature not in unique.get(table, set())
                        or any(item[4].casefold() != "binary" for item in fields)):
                    raise ValueError(f"Requirements DB structure has unsupported unique constraint: {table}")
                indexes.add(signature)
        if not unique.get(table, set()).issubset(indexes):
            raise ValueError(f"Requirements DB structure lacks unique keys: {table}")
        groups = {}
        for row in connection.execute(f"PRAGMA foreign_key_list('{table}')"):
            groups.setdefault(row[0], []).append(tuple(row))
        keys = set()
        for rows in groups.values():
            rows.sort(key=lambda row: row[1])
            if any(row[5:7] != ("NO ACTION", "NO ACTION") for row in rows):
                raise ValueError(f"Requirements DB structure has unsupported foreign keys: {table}")
            keys.add((rows[0][2], tuple(row[3] for row in rows), tuple(row[4] for row in rows)))
        if keys != foreign.get(table, set()):
            raise ValueError(f"Requirements DB structure has incompatible foreign keys: {table}")


def _sql_identity(sql):
    # This closed historical producer uses unquoted ASCII SQL syntax and no
    # string literals. Reject quoted variants; never normalize their meaning.
    if any(character in sql for character in "'\"`["):
        return None
    return sql.translate(str.maketrans("", "", " \t\r\n\f\v")).lower()


def _schema_2_definitions():
    # Literal Unicode whitespace set. Historical semantics do not depend on
    # mutable application validation or the host Unicode database.
    whitespace = "char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288)"

    def text(column, exact=True):
        trimmed = f"trim({column},{whitespace})"
        checks = f"length({trimmed})>0"
        if exact:
            checks += f" AND {column}={trimmed}"
        return f"{column} TEXT NOT NULL COLLATE BINARY CHECK({checks})"

    definitions = {}
    definitions["domain_areas"] = (
        "CREATE TABLE domain_areas("
        + text("id") + "," + text("business_name") + ","
        + text("definition", False) + ",PRIMARY KEY(id),UNIQUE(business_name))"
    )
    for table in ("domain_entities", "domain_actions"):
        definitions[table] = (
            f"CREATE TABLE {table}("
            + text("id") + "," + text("domain_id") + ","
            + text("business_name") + "," + text("code_name") + ","
            + text("definition", False)
            + ",PRIMARY KEY(id),UNIQUE(domain_id,id),"
            "UNIQUE(domain_id,business_name),UNIQUE(domain_id,code_name),"
            "FOREIGN KEY(domain_id) REFERENCES domain_areas(id))"
        )
    for table, name, parent in (
        ("requirement_entities", "entity_id", "domain_entities"),
        ("requirement_actions", "action_id", "domain_actions"),
    ):
        definitions[table] = (
            f"CREATE TABLE {table}("
            "requirement_id TEXT NOT NULL,domain_id TEXT NOT NULL,"
            f"{name} TEXT NOT NULL,PRIMARY KEY(requirement_id,domain_id,{name}),"
            "FOREIGN KEY(requirement_id) REFERENCES requirements(id) ON DELETE NO ACTION,"
            f"FOREIGN KEY(domain_id,{name}) REFERENCES {parent}(domain_id,id))"
        )
    return definitions


def schema_2(connection):
    for statement in _schema_2_definitions().values():
        connection.execute(statement)
    connection.execute("PRAGMA user_version=2")
