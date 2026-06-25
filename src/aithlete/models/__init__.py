"""Pydantic models — the JSON contract between Python and the agent.

Every persisted model carries ``schema_version`` and every metric carries a
provenance/confidence flag (measured / estimated / unknown) plus a source.
"""
