"""SQL for validated Requirements CRUD; caller owns connection and transaction."""
from ..modules.requirements_registry.crud import (
    ENTITIES, RELATIONS, normalize_selection, require, validate_cursor, validate_row,
)


def _where(filters, alias=""):
    terms, values = [], []
    for column, condition in filters.items():
        field = f"{alias}{column}"
        operator, value = next(iter(condition.items()))
        if operator == "eq":
            terms.append(f"{field}=?")
            values.append(value)
        elif value:
            terms.append(f"{field} IN ({','.join('?' for _ in value)})")
            values.extend(value)
        else:
            terms.append("0")
    return " AND ".join(terms) or "1", values


def select_entities(connection, table, filters, level=None):
    where, values = _where(filters)
    if level is not None:
        where += " AND level=?"
        values.append(level)
    return [dict(row) for row in connection.execute(
        f"SELECT {','.join(ENTITIES[table])} FROM {table} WHERE {where} ORDER BY id COLLATE BINARY",
        values,
    )]


def _endpoint(connection, item, name):
    operand = item[name]
    role = operand["endpoint"]
    table = RELATIONS[item["table"]][1][role]
    level = role if item["table"] == "requirement_links" else None
    return select_entities(connection, table, operand.get("filter", {}), level)


def _pair(table, first_role, first, second):
    columns, roles = RELATIONS[table]
    ordered = {first_role: first, next(role for role in roles if role != first_role): second}
    if table == "requirement_links":
        return (ordered["system"]["id"], ordered["application"]["id"])
    role = "entity" if table == "requirement_entities" else "action"
    name = ordered[role]
    return ordered["requirement"]["id"], name["domain_id"], name["id"]


def _a(connection, item, context):
    rows = _endpoint(connection, item, "a")
    require(len(rows) == 1, "operand_cardinality", context)
    return rows[0]


def _relation_query(connection, item, context):
    a = _a(connection, item, context)
    table = item["table"]
    columns, roles = RELATIONS[table]
    a_role, b_role = item["a"]["endpoint"], item["b"]["endpoint"]
    b_table = roles[b_role]
    if table == "requirement_links":
        a_column = "system_id" if a_role == "system" else "application_id"
        b_column = "system_id" if b_role == "system" else "application_id"
        join = f"r.{b_column}=b.id"
        fixed, values = f"r.{a_column}=? AND b.level=?", [a["id"], b_role]
    else:
        name_column = columns[2]
        if a_role == "requirement":
            join = f"r.{name_column}=b.id AND r.domain_id=b.domain_id"
            fixed, values = "r.requirement_id=?", [a["id"]]
        else:
            join = "r.requirement_id=b.id"
            fixed, values = f"r.{name_column}=? AND r.domain_id=?", [a["id"], a["domain_id"]]
    predicate, arguments = _where(item["b"].get("filter", {}), "b.")
    return f"{table} r JOIN {b_table} b ON {join}", f"{fixed} AND {predicate}", values + arguments


def query_page(connection, item, revision, maximum, context):
    key = validate_cursor(item, revision, maximum, context)
    table = item["table"]
    if item["kind"] == "entities":
        columns, keys = ENTITIES[table], ("id",)
        source, alias = table, ""
        where, values = _where(item["filter"])
    else:
        columns = keys = RELATIONS[table][0]
        source, where, values = _relation_query(connection, item, context)
        alias = "r."
    projection = ",".join(f"{alias}{c}" for c in columns)
    if key is not None:
        membership = " AND ".join(f"{alias}{c}=?" for c in keys)
        found = connection.execute(
            f"SELECT {alias}{keys[0]} FROM {source} WHERE {where} AND {membership}", values + key,
        ).fetchone()
        require(found is not None, "invalid_cursor", context)
        fields = ",".join(f"{alias}{c}" for c in keys)
        where += f" AND ({fields})>({','.join('?' for _ in keys)})"
        values += key
    order = ",".join(f"{alias}{c} COLLATE BINARY" for c in keys)
    limit = item["page"]["limit"]
    rows = [dict(row) for row in connection.execute(
        f"SELECT {projection} FROM {source} WHERE {where} ORDER BY {order} LIMIT ?",
        values + [limit + 1],
    )]
    more = len(rows) > limit
    rows = rows[:limit]
    cursor = {"revision": revision, "selection": normalize_selection(item),
              "key": [rows[-1][c] for c in keys]} if more else None
    return {"rows": rows, "next": cursor}


def execute_operation(connection, item, context):
    table, kind = item["table"], item["kind"]
    if kind in ("link", "unlink"):
        a = _a(connection, item, context)
        count = 0
        columns = RELATIONS[table][0]
        predicate = " AND ".join(f"{c}=?" for c in columns)
        for b in _endpoint(connection, item, "b"):
            pair = _pair(table, item["a"]["endpoint"], a, b)
            exists = connection.execute(
                f"SELECT {columns[0]} FROM {table} WHERE {predicate}", pair,
            ).fetchone() is not None
            if kind == "link" and not exists:
                connection.execute(
                    f"INSERT INTO {table}({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                    pair,
                )
                count += 1
            elif kind == "unlink" and exists:
                connection.execute(f"DELETE FROM {table} WHERE {predicate}", pair)
                count += 1
        return count
    columns = ENTITIES[table]
    if kind == "create":
        row = item["row"]
        connection.execute(
            f"INSERT INTO {table}({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
            [row[c] for c in columns],
        )
        return 1
    count = 0
    for row in select_entities(connection, table, item["filter"]):
        if kind == "delete":
            connection.execute(f"DELETE FROM {table} WHERE id=?", (row["id"],))
            count += 1
        else:
            candidate = {**row, **item["changes"]}
            validate_row(table, candidate, context, row)
            if candidate != row:
                changed = [c for c in columns if c != "id" and candidate[c] != row[c]]
                connection.execute(
                    f"UPDATE {table} SET {','.join(c + '=?' for c in changed)} WHERE id=?",
                    [candidate[c] for c in changed] + [row["id"]],
                )
                count += 1
    return count
