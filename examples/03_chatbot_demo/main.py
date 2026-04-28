"""Offline chatbot demo: assemble a memory-augmented prompt.

No real LLM call — the demo prints the prompt that *would* be sent so it
runs entirely offline (Constitution Principle IV).

Usage:
    python examples/03_chatbot_demo/main.py
    > my favourite editor is vscode
    > what's my editor preference?
"""

from __future__ import annotations

import os
import sys

from openmem import Memory


def main() -> None:
    url = os.environ.get(
        "PG_URL", "postgresql://postgres:postgres@localhost:5432/postgres"
    )
    mem = Memory(provider="postgres", url=url)
    user_id = "demo-chat"

    print("OMP chatbot demo. Ctrl+D / Ctrl+Z to quit.\n")
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        ctx = mem.context(line, user_id, token_budget=300)
        prompt = (
            "[system] You are a helpful assistant. Use the relevant memory:\n"
            f"{ctx.text}\n\n[user] {line}\n[assistant]"
        )
        print("\n--- prompt that would be sent to the LLM ---")
        print(prompt)
        print("--------------------------------------------\n")
        # Save the new utterance as memory
        mem.add(content=line, user_id=user_id, scope="chat")


if __name__ == "__main__":
    sys.exit(main() or 0)
