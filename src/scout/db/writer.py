# writer.py — Output persistence for Scout pipeline results.
# Purpose: Writes every pipeline artifact to Supabase via sed_writer; Supabase is the system of record.
# Scope: Single top-level write_outputs() dispatcher; no filesystem output.
# Consumers: run.py calls write_outputs(final_state) once after graph.invoke completes.
from scout.state import ScoutState


def write_outputs(state: ScoutState):
    """Persist all pipeline outputs to Supabase via sed_writer.write_outputs_to_sed.
    No files are written under outputs/ and no local SQLite mirror is updated; Supabase is the system of record."""
    from scout.db.sed_writer import write_outputs_to_sed
    return write_outputs_to_sed(state)
