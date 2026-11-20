"""测试公共工具：把 src 加入路径并加载带连字符的包名。"""
import importlib
import sys
from pathlib import Path

SRC = Path(__file__).parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

DATA = Path(__file__).parents[1] / "data"


def load_module(name: str):
    return importlib.import_module(f"compatibility-sessions.{name}")
