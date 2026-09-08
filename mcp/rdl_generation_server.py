"""
rdl_generation_server.py - launcher for the RDL generation MCP server.

The real, single source of truth for this server lives at
``../skills/claude-skills/rdl-generation/scripts/mcp_server.py``, alongside
the ``rdl_builder.py`` and ``validate_rdl.py`` scripts it wraps (it imports
them as plain sibling modules, so it needs to stay next to them). This file
is a thin, no-logic launcher placed here only so all three MCP servers this
project ships are discoverable from one `mcp/` folder.

Unlike ``report_studio_server.py`` and ``pbi_refine_server.py`` in this same
folder, this one is fully standalone and runs as-is: it has no dependency on
the source repository's backend, only on the two sibling scripts noted
above (both dependency-free, Python 3.9+ standard library only).

Run it directly::

    python mcp/rdl_generation_server.py

Or point an MCP client straight at the real file instead of this launcher::

    {
      "mcpServers": {
        "rdl-generation": {
          "command": "python",
          "args": ["/absolute/path/to/skills/claude-skills/rdl-generation/scripts/mcp_server.py"]
        }
      }
    }
"""
from __future__ import annotations

import sys
from pathlib import Path

_REAL_SERVER_DIR = (
    Path(__file__).resolve().parent.parent
    / "skills" / "claude-skills" / "rdl-generation" / "scripts"
)
if str(_REAL_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_REAL_SERVER_DIR))

from mcp_server import (  # noqa: E402  (re-exported for convenience)
    build_rdl,
    field_expression,
    get_example,
    get_json_spec_schema,
    get_reference,
    list_examples,
    list_reference_topics,
    main,
    validate_rdl_file,
)

__all__ = [
    "build_rdl",
    "field_expression",
    "get_example",
    "get_json_spec_schema",
    "get_reference",
    "list_examples",
    "list_reference_topics",
    "main",
    "validate_rdl_file",
]

if __name__ == "__main__":
    main()
