"""与工作目录无关的项目路径。"""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
STORE_DIR = BASE_DIR / "store" / "knowledge"
