import sys
from pathlib import Path

CC_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CC_DIR))
sys.path.insert(0, str(CC_DIR / "scripts"))
