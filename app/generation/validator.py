"""
Validates generated code to ensure it is syntactically correct and complete.
- Python AST parse check
- SQL syntax check (via sqlite3, single connection for FK support)
- HTML structure check
"""
import ast
import sqlite3
import re
from typing import Dict, List


def validate_generated_code(files: Dict[str, str]) -> List[Dict]:
    """
    Validate all generated code files.

    Returns:
        List of validation issues. Empty list = all clean.
    """
    issues = []

    for filename, content in files.items():
        if filename.endswith(".py"):
            issues.extend(_validate_python(filename, content))
        elif filename.endswith(".sql"):
            issues.extend(_validate_sql(filename, content))
        elif filename.endswith(".html"):
            issues.extend(_validate_html(filename, content))

    return issues


def _validate_python(filename: str, content: str) -> List[Dict]:
    """Validate Python syntax using AST parsing."""
    try:
        ast.parse(content)
        return []
    except SyntaxError as e:
        return [{
            "file": filename,
            "type": "python_syntax_error",
            "message": f"Syntax error at line {e.lineno}: {e.msg}",
            "detail": str(e),
        }]


def _validate_sql(filename: str, content: str) -> List[Dict]:
    """Validate SQL by parsing all statements in a single shared sqlite3 connection."""
    # Extract individual CREATE TABLE statements
    statements = re.split(r';\s*(?:\n|$)', content)
    issues = []

    # Use a single shared connection so FK references resolve
    conn = sqlite3.connect(":memory:")
    try:
        for stmt in statements:
            stmt = stmt.strip()
            if not stmt or stmt.startswith("--"):
                continue
            try:
                conn.execute(stmt)
            except Exception as e:
                issues.append({
                    "file": filename,
                    "type": "sql_error",
                    "message": str(e)[:200],
                    "detail": stmt[:200],
                })
    finally:
        conn.close()

    return issues


def _validate_html(filename: str, content: str) -> List[Dict]:
    """Basic HTML structure validation."""
    issues = []

    if "<!DOCTYPE html>" not in content:
        issues.append({
            "file": filename,
            "type": "html_missing_doctype",
            "message": "Missing DOCTYPE declaration",
        })

    if "</html>" not in content:
        issues.append({
            "file": filename,
            "type": "html_missing_close",
            "message": "Missing closing </html> tag",
        })

    return issues
