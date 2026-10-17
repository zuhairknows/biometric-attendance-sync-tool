"""Offline AST-only credential presence check. Run with Python -I -S -B.

Output is limited to two field names and fixed statuses. This checks on-disk
definitions, not credential validity, permissions, or the running service heap.
"""
import ast
from pathlib import Path
import sys


FIELDS = ('ERPNEXT_API_KEY', 'ERPNEXT_API_SECRET')
MAX_BYTES = 1024 * 1024


def passive(node):
    """Recognize inert syntax without evaluating other configuration values."""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(passive(item) for item in node.elts)
    if isinstance(node, ast.Dict):
        return all(key is not None and passive(key) and passive(value)
                   for key, value in zip(node.keys, node.values))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        return isinstance(node.operand, ast.Constant)
    return False


def inspect_source(source):
    tree = ast.parse(source)
    assignments = {field: [] for field in FIELDS}
    uncertain = False
    for statement in tree.body:
        # Any executable/indirect configuration could mutate module globals.
        # Refuse to infer its effect, even when literal assignments also exist.
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
            continue  # Module docstrings cannot override credentials.
        if isinstance(statement, ast.Assign):
            safe = (all(isinstance(target, ast.Name) for target in statement.targets)
                    and passive(statement.value))
        else:
            safe = False
        if not safe:
            uncertain = True
        for node in ast.walk(statement):
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                if node.id in assignments:
                    # Only a direct, unannotated module assignment can certify a value.
                    direct = (isinstance(statement, ast.Assign)
                              and any(node is target for target in statement.targets))
                    assignments[node.id].append(statement.value if direct else None)

    result = {}
    for field, values in assignments.items():
        if len(values) > 1:
            result[field] = 'AMBIGUOUS'
        elif uncertain:
            result[field] = 'DYNAMIC'
        elif not values:
            result[field] = 'MISSING'
        elif isinstance(values[0], ast.Constant) and isinstance(values[0].value, str):
            value = values[0].value
            # Presence is not authentication validity. Never normalize the value.
            result[field] = 'PRESENT_LITERAL' if value.strip() else 'EMPTY'
        else:
            result[field] = 'DYNAMIC'
    return result


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    result = dict.fromkeys(FIELDS, 'DYNAMIC')
    try:
        if len(argv) != 1 or Path(argv[0]).name.lower() != 'local_config.py':
            raise ValueError()
        # Read only; never import, execute, copy, or hash the source file.
        with Path(argv[0]).open('rb') as stream:
            source = stream.read(MAX_BYTES + 1)
        if len(source) > MAX_BYTES:
            raise ValueError()
        result = inspect_source(source.decode('utf-8-sig'))
    except Exception:
        # SyntaxError text can contain credentials. Never render any exception.
        pass
    for field in FIELDS:
        print(field + ': ' + result[field])
    return 0 if all(value == 'PRESENT_LITERAL' for value in result.values()) else 1


if __name__ == '__main__':
    sys.dont_write_bytecode = True
    raise SystemExit(main())
