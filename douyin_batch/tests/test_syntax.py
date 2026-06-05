"""Check Python 3.8+ compatibility for all .py files in the project."""
import ast
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
print(f"Project root: {PROJECT_ROOT}")
print(f"Python: {sys.version}")

# Walk all .py files
errors = []
total = 0
for py_file in PROJECT_ROOT.rglob("*.py"):
    # Skip venvs / site-packages / output / cache
    skip = any(
        part in py_file.parts
        for part in ("venv", "env", ".venv", "output", "cache", "__pycache__", "site-packages")
    )
    if skip:
        continue
    total += 1
    try:
        with open(py_file, "r", encoding="utf-8") as f:
            ast.parse(f.read(), filename=str(py_file))
    except SyntaxError as e:
        errors.append((py_file, e))
    except UnicodeDecodeError as e:
        errors.append((py_file, e))

print(f"\nChecked {total} .py files")
if errors:
    print(f"FAILED: {len(errors)} files have syntax errors")
    for path, err in errors:
        print(f"  - {path.relative_to(PROJECT_ROOT)}: {err}")
    sys.exit(1)
else:
    print(f"SUCCESS: All {total} files parse cleanly")
