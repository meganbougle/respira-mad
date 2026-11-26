"""
test_chat_days.py - checks that the AI assistant answers about the DAY it was asked about.

Bug (26 Nov 2026): "¿cómo estará Aranjuez el sábado 28 por la tarde-noche?" was answered
with get_risk(day="mañana"), i.e. about Friday. Days other than today and tomorrow must use
get_planner. This asks three questions and prints every tool call with its inputs, the answer,
and whether the right tool was used.

It needs your Anthropic key (same as test_chat.py: a .env file or ANTHROPIC_API_KEY).
Run it with:   .venv/bin/python test_chat_days.py      (3 questions, well under one cent)
"""

import logging

import streamlit  # noqa: F401

logging.getLogger("streamlit.runtime.caching.cache_data_api").setLevel(logging.ERROR)

import assistant  # noqa: E402
import config     # noqa: E402

# question, the tool that must answer it, and (for get_risk) the "day" it must ask for
QUESTIONS = [
    ("¿Cómo estará Aranjuez el sábado por la tarde-noche?", "get_planner", None),
    ("¿Y pasado mañana por la mañana en Getafe?", "get_planner", None),
    ("¿Puedo salir mañana a las 20:00 en Aranjuez?", "get_risk", "mañana"),
]
# what the panel would send: the chosen place and profile, and the pollen types selected by default
PANEL = {"lat": 40.4168, "lon": -3.7038, "place": "Madrid (Centro)", "profile": "general",
         "allergy_types": list(config.POLLEN_DEFAULT_TYPES), "demo": False}

if not assistant.is_available():
    print("The assistant is not available: ANTHROPIC_API_KEY is missing (or AI_ASSISTANT_ENABLED is False).")
    print("Create a .env file with your key (see test_chat.py) and run again.")
    raise SystemExit(1)

# Record every tool call with its inputs (answer() only reports the tool names).
calls = []
real_run_tool = assistant.run_tool


def recording_run_tool(name, tool_input, context):
    calls.append((name, dict(tool_input)))
    return real_run_tool(name, tool_input, context)


assistant.run_tool = recording_run_tool

print(assistant._context_text(PANEL).splitlines()[0])
failures = 0
for question, expected_tool, expected_day in QUESTIONS:
    calls.clear()
    result = assistant.answer([{"role": "user", "content": question}], PANEL, visitor="test")
    print(f"\nP: {question}")
    for name, tool_input in calls:
        shown = {k: v for k, v in tool_input.items() if k not in ("allergy_types", "pollen_types")}
        print(f"   herramienta: {name} {shown}")
    print(f"R: {result['reply']}")
    names = [name for name, _ in calls]
    ok = expected_tool in names
    if expected_tool == "get_planner":
        ok = ok and "get_risk" not in names                       # must not answer from get_risk
    else:
        ok = ok and any(i.get("day") == expected_day for n, i in calls if n == "get_risk")
    failures += not ok
    print(f"   {'OK  ' if ok else 'FAIL'} se esperaba {expected_tool}" + (f' con day="{expected_day}"' if expected_day else ""))

print("\nALL CHECKS PASSED" if not failures else f"\n{failures} CHECK(S) FAILED")
