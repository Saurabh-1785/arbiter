#!/usr/bin/env python3
"""Helper script to install dependencies and run tests."""
import subprocess
import sys

def run(cmd, **kwargs):
    print(f"$ {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, **kwargs)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result.returncode

# Install dependencies
rc = run("pip install fastapi uvicorn websockets networkx pydantic pytest pytest-asyncio httpx")
if rc != 0:
    print("Trying pip3...")
    run("pip3 install fastapi uvicorn websockets networkx pydantic pytest pytest-asyncio httpx")

# Run tests
print("\n" + "="*60)
print("Running tests...")
print("="*60 + "\n")
run("python3 -m pytest tests/ -v", cwd="/home/Saurabh/arbiter")
