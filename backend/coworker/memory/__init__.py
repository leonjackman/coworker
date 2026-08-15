"""Long-term memory subsystem (file-based, human-editable, multi-agent ready).

The memory library lives under ``{COWORKER_DATA_DIR}/memory/`` and is organized
as a directory tree of plain Markdown files — one system level, one directory
per project (timestamp-named ``memory_dir``), and one directory per agent with
core files (SOUL/AGENT/MEMORY) plus a SESSIONS folder for topic-organized
session memory. Layout mirrors ``coworker.skills``: discovery + store + manager
+ middleware. Files are the single source of truth and stay readable/editable
in any editor; writes go through the dedicated ``memory`` tool + validated
``rel`` paths so the memory root boundary is never weakened.
"""
