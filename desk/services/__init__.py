"""Domain services.

Everything in this package must be importable and testable without Streamlit.
No module here performs network access directly: connectors do that, and only
after ``source_policy`` has cleared the request.
"""
