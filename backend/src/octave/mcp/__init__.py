"""MCP client core — typed façade over the official mcp SDK.

Quarantine rule (see design spec): only ``client.py`` and ``transport.py``
may import ``mcp``. Everything else here is Octave-owned and SDK-free.
"""
