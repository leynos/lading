"""The Cyclopts application shared by the CLI command adapters.

Keeping the application object in its own module lets the entry point and the
command adapters register with and dispatch through the same instance without
an import cycle.
"""

from cyclopts import App

__all__ = ["app", "create_app"]


# Cyclopts 4 defaults to ``print_non_int_sys_exit``: it renders the command's
# return value through Rich and calls :func:`sys.exit`, so an embedded caller
# can neither read the result nor control the stream. lading owns that boundary:
# :func:`_dispatch_and_print` prints the raw result once and maps it to an exit
# code, which keeps summaries unwrapped and lets tests call ``app`` directly.
def create_app() -> App:
    """Return a new Cyclopts application with the lading defaults."""
    return App(
        help="Manage Rust workspaces with the lading toolkit.",
        result_action="return_value",
    )


app = create_app()
