"""Conservative contradiction checks; unknown expressions remain executable."""
import ast
import itertools


def contradictory_formula(source: str) -> bool:
    try:
        tree = ast.parse(source, mode="eval").body
    except (SyntaxError, TypeError):
        return False

    def branches(node):
        if isinstance(node, ast.BoolOp):
            groups = [branches(child) for child in node.values]
            if isinstance(node.op, ast.Or):
                result = list(itertools.chain.from_iterable(groups))
            else:
                if any(not group for group in groups):
                    return []
                count = 1
                for group in groups:
                    count *= len(group)
                if count > 64:
                    raise OverflowError
                result = [list(itertools.chain.from_iterable(parts)) for parts in itertools.product(*groups)]
            if len(result) > 64:
                raise OverflowError
            return result
        return [[node]]

    def number(node):
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            value = number(node.operand)
            return None if value is None else (-value if isinstance(node.op, ast.USub) else value)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        return None

    def impossible(atoms):
        bounds = {}
        for atom in atoms:
            if not isinstance(atom, ast.Compare):
                continue
            left = atom.left
            for op, right in zip(atom.ops, atom.comparators, strict=True):
                key, value, operation = left, number(right), type(op)
                if value is None and number(left) is not None:
                    key, value = right, number(left)
                    operation = {ast.Lt: ast.Gt, ast.LtE: ast.GtE, ast.Gt: ast.Lt, ast.GtE: ast.LtE}.get(operation, operation)
                if value is not None and isinstance(key, (ast.Name, ast.Call)):
                    record = bounds.setdefault(ast.dump(key), [])
                    record.append((operation, value))
                left = right
        for constraints in bounds.values():
            lower, upper = float('-inf'), float('inf')
            lower_strict = upper_strict = False
            excluded = set()
            for op, value in constraints:
                if op in (ast.Gt, ast.GtE, ast.Eq):
                    if value > lower:
                        lower, lower_strict = value, op is ast.Gt
                    elif value == lower:
                        lower_strict |= op is ast.Gt
                if op in (ast.Lt, ast.LtE, ast.Eq):
                    if value < upper:
                        upper, upper_strict = value, op is ast.Lt
                    elif value == upper:
                        upper_strict |= op is ast.Lt
                if op is ast.NotEq:
                    excluded.add(value)
            if lower > upper or (lower == upper and (lower_strict or upper_strict or lower in excluded)):
                return True
        return False

    try:
        return all(impossible(branch) for branch in branches(tree))
    except (OverflowError, RecursionError):
        return False
