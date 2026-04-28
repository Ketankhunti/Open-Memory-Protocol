# Chatbot demo

Minimal offline demo of memory-augmented prompting. Runs without any
LLM API key — instead of sending the assembled prompt, it prints it.

```powershell
$env:PG_URL = "postgresql://postgres:postgres@localhost:5432/postgres"
python examples/03_chatbot_demo/main.py
```

Each line you type:

1. Calls `mem.context(line, user_id, token_budget=300)`.
2. Prints the assembled prompt that would be sent to the LLM.
3. Saves the line as a new memory under `scope="chat"`.

Future memories influence later context lookups.
