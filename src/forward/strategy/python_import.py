"""Compile a constrained Python signal function into the shared rule engine.

Uploaded modules are parsed, never imported or executed. This intentionally
supports calculations and conditions, not arbitrary Python programs.
"""
from __future__ import annotations

import ast
import copy

from forward.signals.formula import (
    _ALLOWED_FUNCTIONS,
    INDICATOR_NAMES,
    FormulaError,
    compile_formula,
)
from forward.signals.requirements import formula_requirements


class PythonStrategyError(ValueError):
    pass


def fail(node, message):
    raise PythonStrategyError(f"Line {getattr(node, 'lineno', 1)}: {message}")


def compile_python_strategy(source: str) -> dict:
    if not isinstance(source, str) or not source.strip():
        raise PythonStrategyError("Import a .py file or enter your strategy code.")
    if len(source.encode("utf-8")) > 32000:
        raise PythonStrategyError("Python source must be smaller than 32 KB.")
    try:
        module = ast.parse(source)
    except SyntaxError as exc:
        raise PythonStrategyError(f"Line {exc.lineno}: {exc.msg}") from exc
    statements = [n for n in module.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str))]
    if len(statements) != 1 or not isinstance(statements[0], ast.FunctionDef):
        fail(module, "Provide one function: def strategy(ctx). Imports and module-level code are not supported.")
    fn = statements[0]
    args = fn.args
    if fn.name != "strategy" or fn.decorator_list or len(args.args) != 1 or args.args[0].arg != "ctx" or args.posonlyargs or args.kwonlyargs or args.vararg or args.kwarg or args.defaults:
        fail(fn, "Use def strategy(ctx) with one argument and no decorators.")
    aliases = {}

    class Expand(ast.NodeTransformer):
        def visit_Attribute(self, node):
            if isinstance(node.value, ast.Name) and node.value.id == "ctx" and not node.attr.startswith("_"):
                if node.attr not in INDICATOR_NAMES and node.attr not in _ALLOWED_FUNCTIONS:
                    fail(node, f"Unknown indicator: {node.attr}")
                return ast.copy_location(ast.Name(id=node.attr, ctx=ast.Load()), node)
            fail(node, "Read indicators through ctx only, such as ctx.price or ctx.sma(20).")

        def visit_Name(self, node):
            if node.id in aliases:
                return copy.deepcopy(aliases[node.id])
            if node.id not in INDICATOR_NAMES and node.id not in _ALLOWED_FUNCTIONS:
                fail(node, f"Unknown indicator or variable: {node.id}")
            return node

    def expression(node):
        expanded = Expand().visit(copy.deepcopy(node))
        ast.fix_missing_locations(expanded)
        text = ast.unparse(expanded)
        if len(text) > 2000:
            fail(node, "Expanded condition is too long. Simplify the calculation.")
        try:
            compile_formula(text)
        except FormulaError as exc:
            fail(node, str(exc))
        return expanded

    returned = None
    body = fn.body
    for index, statement in enumerate(body):
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str):
            continue
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
            name = statement.targets[0].id
            if name == "ctx" or name.startswith("_") or name in INDICATOR_NAMES or name in _ALLOWED_FUNCTIONS:
                fail(statement, "Use a local variable name that does not replace an indicator.")
            aliases[name] = expression(statement.value)
        elif isinstance(statement, ast.Return) and index == len(body) - 1 and isinstance(statement.value, ast.Dict):
            returned = statement.value
        else:
            fail(statement, "Use assignments followed by a dictionary with entry and exit conditions. Loops, imports and statement branches are not supported; conditional expressions are allowed.")
    if returned is None:
        fail(fn, "Return a dictionary containing entry and exit conditions.")
    keys = [key.value if isinstance(key, ast.Constant) else None for key in returned.keys]
    if len(keys) != 2 or set(keys) != {"entry", "exit"}:
        fail(returned, "Return exactly two keys: entry and exit.")
    formulas = {
        key: ast.unparse(expression(value))
        for key, value in zip(keys, returned.values, strict=True)
    }
    history = max(3, *(formula_requirements(value)[1] for value in formulas.values()))
    return {"entry": {"setup": "custom_entry", "custom_entry_formula": formulas["entry"], "min_history_bars": history}, "exit": {"custom_exit_formula": formulas["exit"], "min_history_bars": history, "rsi_overbought": None, "trend_sma": 0, "exit_at_week_end": False, "stop_loss_pct": None, "take_profit_pct": None, "trailing_stop_pct": None}}


def code_rules(definition):
    code = definition.get("code")
    if not isinstance(code, dict) or code.get("language") != "python" or code.get("interface_version") != 1:
        raise PythonStrategyError("Python code needs language=python and interface_version=1.")
    return compile_python_strategy(code.get("source"))
