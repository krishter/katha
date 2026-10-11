"""
Assembling first-person Stories from per-turn fragments.

See docs/proposals/first-person-stories.md. Phase A runs this offline over
existing transcripts (backend/scripts/consolidate_stories.py) and writes a
report for human review; nothing here touches the database. Phase B moves
the same pipeline into close_and_process_session, which is why the logic
lives in a package rather than inside the script.
"""
