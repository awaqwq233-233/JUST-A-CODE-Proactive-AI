"""执行前校验当前工具契约，畸形 JSON 不能退化为默认参数。"""


def validate_arguments(schema, arguments):
    """校验对象、必填项、未知键、基本类型和枚举；失败时拒绝执行。"""
    if not isinstance(arguments, dict):
        raise ValueError("工具参数须为 JSON 对象")
    properties = schema.get("properties", {})
    if set(arguments) - set(properties):
        raise ValueError("工具参数包含未声明字段")
    if set(schema.get("required", [])) - set(arguments):
        raise ValueError("工具参数缺少必填字段")
    for key, value in arguments.items():
        spec = properties[key]
        kind = spec.get("type")
        valid = {
            "string": isinstance(value, str),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
            "object": isinstance(value, dict),
            "array": isinstance(value, list),
        }
        if kind not in valid or not valid[kind]:
            raise ValueError(f"工具字段 {key} 类型错误")
        if "enum" in spec and value not in spec["enum"]:
            raise ValueError(f"工具字段 {key} 不在允许枚举内")
