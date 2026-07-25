import os
import sys
from pathlib import Path

# Add src to path so tests can import infralink without install
src_path = str(Path(__file__).parent / "src")
sys.path.insert(0, src_path)
os.environ["PYTHONPATH"] = os.pathsep.join(
    path for path in (src_path, os.environ.get("PYTHONPATH")) if path
)
