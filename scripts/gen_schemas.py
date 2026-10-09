"""Regenerate docs/SCHEMAS.md from the pydantic models: uv run python scripts/gen_schemas.py"""

from pathlib import Path

from service015.expert.schemas_doc import render

OUT = Path(__file__).resolve().parents[1] / "docs" / "SCHEMAS.md"

if __name__ == "__main__":
    OUT.write_text(render(), encoding="utf-8", newline="\n")
    print(f"wrote {OUT}")
