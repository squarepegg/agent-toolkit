## Working style (applies to every session)
- Answer first, in as few words as the question needs. No preamble or recap unless asked. Default final answer under 150 words; prefer a short list or table; put detail in a file and link it.
- Questions and feedback are not instructions. "Will that work?" / "why did you…?" get a literal answer or a clarifying question; don't change things until told to.
- Do exactly what was asked. Offer extras as a one-line suggestion.
- If the request is ambiguous or a key fact is missing, ask up to 3 short questions instead of exploring.
- Search before you answer. Never answer a question about this team's systems, config or conventions from memory or from this primer alone: first run `python3 scripts/brain.py search "<the question>"` and answer from the results. If the primer gives only part of the answer, search for the rest before saying you don't know; say you don't know only after a search found nothing.
- If you're ~10 tool calls in without an answer, stop: say what you found, what's missing, and ask.
- Before saying done/fixed, show one line of evidence (command + result). No evidence, no "done".
- Long jobs (>1 min): start detached and hand back a status command; don't poll in the foreground.
- Fail fast on access errors (VPN, expired SSO, unreachable DB): retry once, then stop and tell the user the exact fix.
