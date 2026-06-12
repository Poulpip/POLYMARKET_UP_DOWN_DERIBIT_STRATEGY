---
name: history
description: Fetches and analyzes your Polymarket trading performance over a specific time window, OR parses raw UI copy-paste data to get your exact PNL.
---

When the user invokes the `/history` command, your goal is to provide a comprehensive report of their trading performance. 

## Two Modes of Operation

### Mode 1: Automated Blockchain Fetch (100% Exact)
If the user provides a time window (e.g., `/history 24h` or `/history 7d`), run the official python script:
```bash
cd /home/adry7/bot-v2 && ./bot_env/bin/python .agents/skills/history/scripts/fetch_history.py --window <TIME_WINDOW>
```
**REQUIREMENT:** The `fetch_history.py` script queries the raw Polymarket Subgraphs (The Graph) to capture all AMM swaps, CLOB trades, and automatic redemptions accurately. It requires the user to have `GRAPH_API_KEY` configured in their `.env` file. If they do not have it, the script will abort and instruct them to generate a free key.

### Mode 2: UI Parser (Exact PNL)
If the user pastes raw UI text into the chat and asks for history:
1. Save the pasted text to a file in the workspace (e.g. `raw_history.txt`).
2. Run the UI parser script:
```bash
cd /home/adry7/bot-v2 && ./bot_env/bin/python .agents/skills/history/scripts/parse_ui_history.py raw_history.txt
```
3. Report the EXACT PNL provided by the script.

