"""Module entry point so ``python -m cli.forge_cli`` works (MET-556).

Delegates to :func:`cli.forge_cli.main.main`, matching the invocation
documented in the README and ``docs/cli-reference.md``.
"""

from cli.forge_cli.main import main

if __name__ == "__main__":
    main()
