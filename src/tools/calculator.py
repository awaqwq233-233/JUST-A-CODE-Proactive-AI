"""有界纯算术工具；不执行Python语句、名称、属性或函数调用。"""
import ast
import math
import operator


def calculate(expression):
    """只计算有限数值和基础运算，限制语法树、指数和结果大小。"""
    if not isinstance(expression, str) or not 1 <= len(expression) <= 200:
        raise ValueError('算式长度须为1–200字')
    tree = ast.parse(expression, mode='eval')
    if len(list(ast.walk(tree))) > 48:
        raise ValueError('算式过于复杂')
    binary = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
              ast.Div: operator.truediv, ast.Mod: operator.mod, ast.FloorDiv: operator.floordiv, ast.Pow: operator.pow}

    def evaluate(node):
        """递归计算白名单节点，执行前限制指数，执行后核对有限范围。"""
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
            value = node.value
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = evaluate(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
        elif isinstance(node, ast.BinOp) and type(node.op) in binary:
            left, right = evaluate(node.left), evaluate(node.right)
            if isinstance(node.op, ast.Pow) and (abs(right) > 12 or abs(left) > 1000000):
                raise ValueError('幂运算超过预算')
            value = binary[type(node.op)](left, right)
        else:
            raise ValueError('只允许数字和加减乘除、取余、整除、幂及括号')
        if type(value) not in {int, float} or not math.isfinite(value) or abs(value) > 1e100:
            raise ValueError('算术结果超限或非有限数值')
        return value

    return {'expression': expression, 'result': evaluate(tree), 'precision': '整数精确；小数使用Python浮点精度'}
