# T-S3 dev-supporting tests — make scripts/gate.py importable as `gate`.
# NOTE for QA: this directory exists only because the dev branch must not
# commit anything under scripts/tests/ (that's QA's, joined at merge time).
# Please relocate these files into scripts/tests/ at merge.
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
