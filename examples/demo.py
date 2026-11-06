"""Quick demo of the venue scheduling demand change tool.

Run with: python examples/demo.py
"""

import sys
import os

# Allow running from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from my_tool.main import main

if __name__ == "__main__":
    sys.exit(main())
