"""Hermes Flow: recorded agent flows and graphs."""


def register(ctx):
    from .cli import setup_parser, dispatch
    ctx.register_cli_command(name="flow", help="Show recorded agent flows and graphs",
                             setup_fn=setup_parser, handler_fn=dispatch)
