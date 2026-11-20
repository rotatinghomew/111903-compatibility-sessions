"""显示项目基本信息。"""

from . import __version__
from .cli import main as _cli_main


def main() -> None:
    raise SystemExit(_cli_main())


if __name__ == "__main__":
    main()
