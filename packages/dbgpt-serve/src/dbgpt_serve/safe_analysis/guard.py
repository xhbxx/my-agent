"""Parse once, resolve scopes, authorize physical sources, then cap the result.

SQLGlot is an application guard, not a database security boundary. The executor
also requires a dedicated database account and read-only transactions.
"""

import hashlib

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope

from .contracts import AnalysisError, SourcePolicy, ValidatedQuery

# Deliberately small: add a function only with dialect-specific tests.
SAFE_FUNCTIONS = {
    "SUM",
    "AVG",
    "COUNT",
    "MIN",
    "MAX",
    "ROUND",
    "ABS",
    "COALESCE",
    "NULLIF",
    "LOWER",
    "UPPER",
    "LENGTH",
    "CAST",
    "EXTRACT",
    "DATE_TRUNC",
    "TIMESTAMP_TRUNC",
    "STRFTIME",
    "TIME_TO_STR",
    "CASE",
    "IF",
    "EXISTS",
}
FORBIDDEN_NODES = {
    "Insert",
    "Update",
    "Delete",
    "Merge",
    "Create",
    "Drop",
    "Alter",
    "Command",
    "Copy",
    "Into",
    "Lock",
    "Transaction",
    "Commit",
    "Rollback",
    "Grant",
    "Revoke",
    "Set",
    "Use",
    "Execute",
    "Pragma",
    "Attach",
    "Detach",
    "Hint",
    "TableSample",
    "Pivot",
    "Unpivot",
    "Collate",
    "SessionParameter",
    "Parameter",
    "Placeholder",
    "PropertyEQ",
    "MatchAgainst",
    "WithTableHint",
}


def visible_schema(policy: SourcePolicy) -> dict:
    """Exclude raw sensitive columns everywhere, including WHERE and ORDER BY.

    To analyze a sensitive value, expose a pre-masked database view instead.
    Output-label masking alone cannot stop `substr(secret, ...) AS innocuous`.
    """
    blocked = {name.lower() for name in policy.blocked_columns}
    return {
        table: {
            name: kind for name, kind in columns.items() if name.lower() not in blocked
        }
        for table, columns in policy.tables.items()
    }


def validate_sql(sql: str, policy: SourcePolicy) -> ValidatedQuery:
    try:
        return _validate(sql, policy)
    except AnalysisError:
        raise
    except Exception as exc:
        # Parser errors can contain SQL literals; don't put them in logs/responses.
        raise AnalysisError("SQL 解析失败，或表、字段、别名无法在白名单中解析") from exc


def _validate(sql: str, policy: SourcePolicy) -> ValidatedQuery:
    if len(sql) > 20000:
        raise AnalysisError("SQL 超过长度限制")
    statements = sqlglot.parse(sql, read=policy.dialect)
    if len(statements) != 1 or not isinstance(statements[0], (exp.Select, exp.Union)):
        raise AnalysisError("只允许一条 SELECT/UNION 查询")
    tree = statements[0]
    nodes = list(tree.walk())
    if len(nodes) > 2000:
        raise AnalysisError("SQL 结构过于复杂")
    for node in nodes:
        if type(node).__name__ in FORBIDDEN_NODES:
            raise AnalysisError("SQL 包含写入、锁或不支持的高风险语法")
        if isinstance(node, exp.With) and node.args.get("recursive"):
            raise AnalysisError("不允许递归 CTE")
        if isinstance(node, exp.Func):
            name = (
                node.name.upper()
                if isinstance(node, exp.Anonymous)
                else node.sql_name()
            )
            if name not in SAFE_FUNCTIONS or isinstance(node.parent, exp.Dot):
                raise AnalysisError("SQL 使用了未授权函数")
        if (
            isinstance(node, exp.DataType)
            and node.this == exp.DataType.Type.USERDEFINED
        ):
            raise AnalysisError("不允许自定义类型转换")

    schema = visible_schema(policy)
    physical = []
    for scope in traverse_scope(tree):
        for _, source in scope.selected_sources.values():
            if not isinstance(source, exp.Table):
                continue  # A CTE/derived table has its own scope, visited separately.
            if not isinstance(source.this, exp.Identifier):
                raise AnalysisError("不允许表函数和动态表名")
            if source.catalog or (source.db and source.db != policy.schema):
                raise AnalysisError("不允许跨 schema 或跨数据库访问")
            if source.name not in schema or not schema[source.name]:
                raise AnalysisError("查询引用了未授权表")
            # Resolve exactly the configured schema; never rely on search_path.
            source.set("db", exp.to_identifier(policy.schema, quoted=True))
            physical.append(source.name)
    if not physical:
        raise AnalysisError("查询必须引用至少一张授权表")

    tree = qualify(
        tree,
        dialect=policy.dialect,
        schema={policy.schema: schema},
        validate_qualify_columns=True,
        quote_identifiers=True,
        identify=True,
    )
    # qualify resolves names but is not itself a column permission checker.
    for scope in traverse_scope(tree):
        for column in scope.columns:
            source = scope.sources.get(column.table)
            if isinstance(source, exp.Table):
                if column.name not in schema[source.name]:
                    raise AnalysisError("查询引用了未授权字段")
    if any(not isinstance(star.parent, exp.Count) for star in tree.find_all(exp.Star)):
        raise AnalysisError("无法安全展开通配符")
    if len(tree.named_selects) > 50:
        raise AnalysisError("输出字段超过 50 列，请缩小查询范围")
    limit = tree.args.get("limit")
    if limit is not None:
        value = limit.expression
        if (
            not isinstance(value, exp.Literal)
            or not value.is_int
            or int(value.this) < 0
        ):
            raise AnalysisError("LIMIT 必须是非负整数")
        cap = min(int(value.this), policy.max_rows + 1)
    else:
        cap = policy.max_rows + 1
    offset = tree.args.get("offset")
    if offset is not None:
        value = offset.expression
        if not isinstance(value, exp.Literal) or not value.is_int:
            raise AnalysisError("OFFSET 必须是整数")
        if not 0 <= int(value.this) <= 10000:
            raise AnalysisError("OFFSET 超过允许范围")
    tree = tree.limit(cap)
    rendered = tree.sql(
        dialect=policy.dialect, unsupported_level=sqlglot.ErrorLevel.RAISE
    )
    return ValidatedQuery(
        rendered,
        hashlib.sha256(rendered.encode()).hexdigest(),
        tuple(sorted(set(physical))),
    )
