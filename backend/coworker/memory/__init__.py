"""Long-term memory subsystem (file-based, human-editable).

Layout mirrors ``coworker.skills``: discovery + store + manager + middleware.
Memory is stored as Markdown files (``MEMORY.md``) with ``§``-separated
entries — one project-level file under the workspace, one user-level file
under the home directory. Files are the single source of truth: the agent
never holds paths to them (writes go through the dedicated ``memory`` tool +
fixed whitelist), so the workspace boundary invariant is never weakened.
"""
