"""Regression tests for object_graph.py add_object bugfix (parent_id removal).

Validates:
1. Module can be imported (syntax check - Python 3.12+)
2. IndustrialObject ORM has all required columns but NOT parent_id
3. add_object constructs IndustrialObject with all NOT NULL fields:
   - department_id (from self._dept_id)
   - visibility_scope="tree"
   - owner_user_id (from self._actor_id)
4. Project-wide scan: no other IndustrialObject constructor calls pass parent_id

Run: uv run pytest tests/test_object_graph_regression.py -v
"""

import inspect
import os

# ---- Test 1: Module Import & Type Definitions ----


def test_module_imports_cleanly():
    """Verify package can be imported without errors."""
    from packages.standards.objects import IndustrialObject, ObjectGraphService  # noqa: F401

    assert ObjectGraphService is not None
    assert IndustrialObject is not None


def test_module_syntax_is_valid_python312():
    """Verify syntax is valid Python 3.12+ (ast.parse)."""
    import ast

    filepath = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "..",
        "packages/standards/objects/object_graph.py",
    )
    with open(filepath) as f:
        source = f.read()
    ast.parse(source)  # Raises SyntaxError if invalid


# ---- Test 2: ORM Column Verification ----


def test_industrial_object_has_no_parent_id_column():
    """parent_id must NOT be a column on IndustrialObject (the bug being fixed)."""
    from packages.standards.objects import IndustrialObject

    cols = {c.name for c in IndustrialObject.__table__.columns}
    assert "parent_id" not in cols, (
        "parent_id should not be a column on IndustrialObject ORM entity. "
        "It was removed in the bugfix."
    )


def test_industrial_object_has_all_required_columns():
    """All NOT NULL columns must exist on IndustrialObject."""
    from packages.standards.objects import IndustrialObject

    cols = {c.name for c in IndustrialObject.__table__.columns}

    required = {
        "id",
        "department_id",
        "object_type",
        "code",
        "display_name",
        "status",
        "created_at",
        "updated_at",
        "lock_version",
        "visibility_scope",
        "owner_user_id",
    }
    missing = required - cols
    assert not missing, f"Missing required columns: {missing}"


def test_industrial_object_nullable_constraints():
    """A-class NOT NULL columns must be non-nullable."""
    from packages.standards.objects import IndustrialObject

    col_nullable = {c.name: c.nullable for c in IndustrialObject.__table__.columns}

    for col_name in ("visibility_scope", "owner_user_id", "department_id"):
        assert col_name in col_nullable, f"{col_name} column missing from ORM"
        assert col_nullable[col_name] is False, (
            f"{col_name} must be NOT NULL (violates A-class constraint)"
        )


def test_industrial_object_optional_columns():
    """Optional columns description and equipment_id must exist and be nullable."""
    from packages.standards.objects import IndustrialObject

    col_info = {
        c.name: {"nullable": c.nullable, "type": str(c.type)}
        for c in IndustrialObject.__table__.columns
    }

    for opt_col in ("description", "equipment_id"):
        assert opt_col in col_info, f"Optional column {opt_col} missing"
        assert col_info[opt_col]["nullable"] is True, f"{opt_col} should be nullable"


# ---- Test 3: add_object Constructor Fields ----


def test_add_object_constructs_with_visibility_scope():
    """add_object must pass visibility_scope='tree' to IndustrialObject constructor."""
    from packages.standards.objects.object_graph import ObjectGraphService

    source = inspect.getsource(ObjectGraphService.add_object)
    assert "visibility_scope" in source, (
        "add_object must set visibility_scope in IndustrialObject constructor"
    )
    assert 'visibility_scope="tree"' in source, (
        "add_object must set visibility_scope='tree' in IndustrialObject constructor"
    )


def test_add_object_constructs_with_owner_user_id():
    """add_object must pass owner_user_id=self._actor_id to IndustrialObject constructor."""
    from packages.standards.objects.object_graph import ObjectGraphService

    source = inspect.getsource(ObjectGraphService.add_object)
    assert "owner_user_id" in source, (
        "add_object must set owner_user_id in IndustrialObject constructor"
    )
    assert "owner_user_id=self._actor_id" in source, (
        "add_object must set owner_user_id=self._actor_id in IndustrialObject constructor"
    )


def test_add_object_does_not_pass_parent_id_to_constructor():
    """add_object must NOT pass parent_id= to IndustrialObject constructor.

    parent_id is only used for validation (checking parent existence),
    not passed to the ORM constructor.
    """
    from packages.standards.objects.object_graph import ObjectGraphService

    source = inspect.getsource(ObjectGraphService.add_object)

    # Extract the IndustrialObject(...) constructor call
    # Find the block between "obj = IndustrialObject(" and the matching ")"
    lines = source.split("\n")
    in_constructor = False
    constructor_lines = []
    paren_depth = 0

    for line in lines:
        if "IndustrialObject(" in line and not in_constructor:
            in_constructor = True
            constructor_lines.append(line)
            paren_depth += line.count("(") - line.count(")")
            continue
        if in_constructor:
            constructor_lines.append(line)
            paren_depth += line.count("(") - line.count(")")
            if paren_depth <= 0:
                break

    constructor_text = "\n".join(constructor_lines)

    # parent_id= should NOT appear in the constructor call
    assert "parent_id=" not in constructor_text, (
        f"parent_id= must NOT be passed to IndustrialObject constructor.\n"
        f"Constructor call:\n{constructor_text}"
    )


def test_add_object_parent_id_still_accepted_as_parameter():
    """parent_id is still a parameter (for API compat and validation) but not passed to ORM."""
    from packages.standards.objects.object_graph import ObjectGraphService

    sig = inspect.signature(ObjectGraphService.add_object)
    params = list(sig.parameters.keys())
    assert "parent_id" in params, (
        "parent_id should remain as a method parameter (for validation only)"
    )


def test_add_object_has_all_constructor_fields():
    """Run a field-by-field check on the constructor vs ORM columns."""
    from packages.standards.objects import IndustrialObject
    from packages.standards.objects.object_graph import ObjectGraphService

    source = inspect.getsource(ObjectGraphService.add_object)

    # Collect NOT NULL columns that must be passed (no server_default or default)
    not_null_no_default = []
    for col in IndustrialObject.__table__.columns:
        if not col.nullable and col.server_default is None and col.default is None:
            not_null_no_default.append(col.name)

    # These columns should be explicitly set in the constructor
    # id, department_id, object_type, code, display_name, description(optional),
    # equipment_id(optional), visible_departments, visibility_scope, owner_user_id,
    # status, created_at, updated_at, lock_version
    required_in_constructor = {
        "id",
        "department_id",
        "object_type",
        "code",
        "display_name",
        "status",
        "created_at",
        "updated_at",
        "lock_version",
        "visibility_scope",
        "owner_user_id",
        "visible_departments",
    }

    for field in required_in_constructor:
        assert f"{field}=" in source, (
            f"add_object must pass {field}= to IndustrialObject constructor"
        )


# ---- Test 4: Project-wide Scan for parent_id in IndustrialObject Construction ----


def test_no_other_file_passes_parent_id_to_industrial_object():
    """No other file in the project should pass parent_id= to IndustrialObject constructor.

    Scans all .py files that contain 'IndustrialObject(' for the forbidden parent_id=.
    """
    this_file = os.path.abspath(__file__)
    project_root = os.path.join(os.path.dirname(__file__), "..", "..", "..")

    import subprocess

    # Find all Python files that construct IndustrialObject
    result = subprocess.run(
        ["grep", "-rl", "IndustrialObject(", project_root],
        capture_output=True,
        text=True,
        cwd=project_root,
    )
    files = [
        os.path.abspath(f)
        for f in result.stdout.strip().split("\n")
        if f and f.endswith(".py") and os.path.abspath(f) != this_file
    ]

    violations = []
    for filepath in files:
        try:
            with open(filepath, encoding="utf-8") as f:
                content = f.read()
        except (UnicodeDecodeError, IsADirectoryError):
            continue  # Skip binary files and directories

        # Parse with AST to find IndustrialObject constructor calls
        import ast

        try:
            tree = ast.parse(content, filename=filepath)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                is_ind_obj = (
                    isinstance(node.func, ast.Name) and node.func.id == "IndustrialObject"
                ) or (isinstance(node.func, ast.Attribute) and node.func.attr == "IndustrialObject")
                if is_ind_obj:
                    for kw in node.keywords:
                        if kw.arg == "parent_id":
                            lineno = getattr(kw, "lineno", "?")
                            violations.append(f"{filepath}:{lineno}")

    assert not violations, (
        f"Found {len(violations)} file(s) passing parent_id= to IndustrialObject:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def test_service_class_init_has_actor_id():
    """ObjectGraphService.__init__ must accept and store actor_id."""
    from packages.standards.objects.object_graph import ObjectGraphService

    sig = inspect.signature(ObjectGraphService.__init__)
    params = list(sig.parameters.keys())
    assert (
        "actor_id" in params
        or "self" in params
        and "actor_id" in ["self"] + list(sig.parameters.keys())
    ), "ObjectGraphService must accept actor_id"

    source = inspect.getsource(ObjectGraphService.__init__)
    assert "self._actor_id" in source, "actor_id must be stored as self._actor_id"


# ---- Test 5: Edge Cases & Type Consistency ----


def test_visibility_scope_default_value():
    """visibility_scope must default to 'tree' per A-class requirements."""
    from packages.standards.objects import IndustrialObject

    col = IndustrialObject.__table__.columns["visibility_scope"]
    # Server default should be 'tree'
    server_default_text = str(col.server_default.arg) if col.server_default is not None else None
    # SQLAlchemy default text wraps in quotes: "'tree'"
    assert server_default_text in ("tree", "'tree'"), (
        f"visibility_scope server_default should be 'tree', got {server_default_text}"
    )


def test_department_id_is_not_nullable():
    """department_id is stage 1 NOT NULL."""
    from packages.standards.objects import IndustrialObject

    col = IndustrialObject.__table__.columns["department_id"]
    assert col.nullable is False, "department_id must be NOT NULL (stage 1)"


def test_id_is_uuid_type():
    """id column must be UUID type."""

    from packages.standards.objects import IndustrialObject

    col = IndustrialObject.__table__.columns["id"]
    col_type = col.type
    # GUID is a UUID-based custom type — verify via the type representation
    col_type_str = str(col_type)
    assert "GUID" in col_type_str or "UUID" in col_type_str.upper(), (
        f"id column type should be GUID/UUID, got {col_type_str}"
    )
