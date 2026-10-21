from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "src/main/resources/openapi/marketplace.yaml"
OUTPUT_DIR = ROOT / "src/app/generated"


def reference_name(schema: dict[str, Any]) -> str | None:
    reference = schema.get("$ref")
    return reference.rsplit("/", 1)[-1] if reference else None


def python_type(schema: dict[str, Any], model_prefix: str = "") -> str:
    reference = reference_name(schema)
    if reference:
        result = f"{model_prefix}{reference}"
    elif "enum" in schema:
        values = ", ".join(repr(value) for value in schema["enum"])
        result = f"Literal[{values}]"
    elif schema.get("type") == "string":
        result = {
            "date-time": "datetime",
            "decimal": "Decimal",
            "email": "EmailStr",
            "uuid": "UUID",
        }.get(schema.get("format"), "str")
    elif schema.get("type") == "integer":
        result = "int"
    elif schema.get("type") == "number":
        result = "Decimal" if schema.get("format") == "decimal" else "float"
    elif schema.get("type") == "boolean":
        result = "bool"
    elif schema.get("type") == "array":
        result = f"list[{python_type(schema['items'], model_prefix)}]"
    else:
        result = "dict[str, Any]"
    return f"{result} | None" if schema.get("nullable") else result


def field_arguments(schema: dict[str, Any]) -> list[str]:
    arguments = []
    mapping = {
        "minLength": "min_length",
        "maxLength": "max_length",
        "minItems": "min_length",
        "maxItems": "max_length",
        "pattern": "pattern",
        "minimum": "ge",
        "maximum": "le",
        "exclusiveMinimum": "gt",
        "exclusiveMaximum": "lt",
    }
    for source, target in mapping.items():
        if source not in schema:
            continue
        value = schema[source]
        if schema.get("format") == "decimal" and source in {
            "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"
        }:
            rendered = f'Decimal("{value}")'
        else:
            rendered = repr(value)
        arguments.append(f"{target}={rendered}")
    return arguments


def render_models(specification: dict[str, Any]) -> str:
    schemas = specification["components"]["schemas"]
    lines = [
        "from datetime import datetime",
        "from decimal import Decimal",
        "from enum import Enum",
        "from typing import Any, Literal",
        "from uuid import UUID",
        "",
        "from pydantic import BaseModel, ConfigDict, EmailStr, Field",
        "",
        "",
        "class ApiModel(BaseModel):",
        "    model_config = ConfigDict(json_encoders={Decimal: float})",
        "",
        "",
    ]
    for name, schema in schemas.items():
        if "enum" not in schema:
            continue
        lines.append(f"class {name}(str, Enum):")
        for value in schema["enum"]:
            lines.append(f"    {value} = {value!r}")
        lines.extend(["", ""])
    for name, schema in schemas.items():
        if "enum" in schema:
            continue
        lines.append(f"class {name}(ApiModel):")
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        if not properties:
            lines.append("    pass")
        for field_name, field_schema in properties.items():
            annotation = python_type(field_schema)
            arguments = field_arguments(field_schema)
            if field_name not in required and "default" not in field_schema:
                if "None" not in annotation:
                    annotation = f"{annotation} | None"
                if arguments:
                    assignment = f" = Field(default=None, {', '.join(arguments)})"
                else:
                    assignment = " = None"
            elif "default" in field_schema:
                default = repr(field_schema["default"])
                if arguments:
                    assignment = f" = Field(default={default}, {', '.join(arguments)})"
                else:
                    assignment = f" = {default}"
            elif arguments:
                assignment = f" = Field({', '.join(arguments)})"
            else:
                assignment = ""
            lines.append(f"    {field_name}: {annotation}{assignment}")
        lines.extend(["", ""])
    return "\n".join(lines).rstrip() + "\n"


def resolve_parameter(parameter: dict[str, Any], specification: dict[str, Any]) -> dict[str, Any]:
    reference = reference_name(parameter)
    if reference:
        return specification["components"]["parameters"][reference]
    return parameter


def success_response(operation: dict[str, Any]) -> tuple[int, str | None]:
    for code in ("200", "201", "204"):
        if code not in operation["responses"]:
            continue
        response = operation["responses"][code]
        schema = response.get("content", {}).get("application/json", {}).get("schema", {})
        return int(code), reference_name(schema)
    raise ValueError(f"No success response for {operation['operationId']}")


def render_parameter(parameter: dict[str, Any]) -> tuple[str, str, bool]:
    name = parameter["name"]
    schema = parameter["schema"]
    annotation = python_type(schema, "models.")
    required = parameter.get("required", False)
    arguments = field_arguments(schema)
    if parameter["in"] == "path":
        return f"{name}: {annotation}", name, True
    default = schema.get("default")
    if not required and default is None:
        if "None" not in annotation:
            annotation = f"{annotation} | None"
        default_text = "None"
    else:
        default_text = repr(default) if default is not None else "..."
    query = f"Query(default={default_text}"
    if arguments:
        query += f", {', '.join(arguments)}"
    query += ")"
    return f"{name}: {annotation} = {query}", name, False


def render_router(specification: dict[str, Any]) -> str:
    lines = [
        "from uuid import UUID",
        "",
        "from fastapi import APIRouter, Depends, Query, Response",
        "",
        "from app.generated import models",
        "from app.handlers import dispatch",
        "from app.security import CurrentUser, require_user",
        "",
        "",
        "router = APIRouter()",
        "",
        "",
    ]
    methods = {"get", "post", "put", "delete"}
    for path, path_item in specification["paths"].items():
        common_parameters = path_item.get("parameters", [])
        for method, operation in path_item.items():
            if method not in methods:
                continue
            operation_id = operation["operationId"]
            status_code, response_model = success_response(operation)
            decorator = f'@router.{method}("{path}", status_code={status_code}'
            if response_model:
                decorator += f", response_model=models.{response_model}"
            elif status_code == 204:
                decorator += ", response_class=Response"
            decorator += ")"
            lines.append(decorator)
            required_parameters = []
            optional_parameters = []
            call_arguments = []
            for raw_parameter in common_parameters + operation.get("parameters", []):
                parameter = resolve_parameter(raw_parameter, specification)
                rendered, name, required = render_parameter(parameter)
                (required_parameters if required else optional_parameters).append(rendered)
                call_arguments.append(f"{name}={name}")
            request_schema = operation.get("requestBody", {}).get("content", {}).get(
                "application/json", {}
            ).get("schema", {})
            request_model = reference_name(request_schema)
            if request_model:
                required_parameters.append(f"request: models.{request_model}")
                call_arguments.append("request=request")
            if operation.get("security"):
                required_parameters.append("current_user: CurrentUser = Depends(require_user)")
                call_arguments.append("current_user=current_user")
            parameters = required_parameters + optional_parameters
            lines.append(f"async def {operation_id}({', '.join(parameters)}):")
            arguments = ", ".join(call_arguments)
            lines.append(f'    return dispatch("{operation_id}"{", " if arguments else ""}{arguments})')
            lines.extend(["", ""])
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    specification = yaml.safe_load(SPEC_PATH.read_text())
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "__init__.py").write_text("")
    (OUTPUT_DIR / "models.py").write_text(render_models(specification))
    (OUTPUT_DIR / "router.py").write_text(render_router(specification))


if __name__ == "__main__":
    main()
