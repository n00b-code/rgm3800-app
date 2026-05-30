"""Entry point: GUI by default, CLI when invoked with ``--cli``.

    python -m rgm3800app            # launch the desktop app
    python -m rgm3800app --cli ...  # use the command-line interface
"""

import sys


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "--cli":
        from .cli import main as cli_main
        return cli_main(argv[1:])
    from .app import run_gui
    run_gui()
    return 0


if __name__ == "__main__":
    sys.exit(main())
