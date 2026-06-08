from pathlib import Path

import uvicorn
from dotenv import load_dotenv

_DATA_DIR = Path.home() / ".context-bridge"


def main():
    _DATA_DIR.mkdir(exist_ok=True)
    env_file = _DATA_DIR / ".env"
    if env_file.exists():
        load_dotenv(env_file)
    else:
        load_dotenv()
    print(f"Context Bridge running at http://127.0.0.1:8000")
    print(f"Data stored at: {_DATA_DIR}")
    print(f"Press Ctrl+C to stop.\n")
    uvicorn.run("context_bridge.main:app", host="127.0.0.1", port=8000)
