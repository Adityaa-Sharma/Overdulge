# Contract tests

Stores the MCP tool schemas each platform adapter depends on. QA's weekly
regression (`agents/qa.md` Mode B) fetches live `tools/list` and diffs
against these — schema drift files a P1 bug, not a silent break. Never call
live platforms from CI. See `docs/architecture/SYSTEM.md` §5.
