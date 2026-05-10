"""Lynchpin data sources.

Each module is a self-contained, read-only API over canonical local inputs.
Import source modules directly from `lynchpin.sources`. Any command that writes
artifacts, refreshes exports, or performs network I/O lives under
`lynchpin.analysis` or `lynchpin.cli` instead of hiding side effects here.
"""
