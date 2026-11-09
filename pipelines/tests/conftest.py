import sys
from pathlib import Path

# Make the `data` package (pipelines/data/) importable from tests without
# installing it as part of the wheel.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
