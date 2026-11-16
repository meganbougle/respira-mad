"""
test_chat.py - asks the AI assistant the test questions and prints its answers.

It needs your Anthropic key. Either:
  - create a file called .env in this folder with one line:
        ANTHROPIC_API_KEY=sk-ant-...
    (.env is ignored by git, so the key is never committed), or
  - set the ANTHROPIC_API_KEY environment variable.

Run it with:   .venv/bin/python test_chat.py
It makes 8 real requests to Anthropic (a fraction of a cent in total).
"""

import logging

import streamlit  # noqa: F401

logging.getLogger("streamlit.runtime.caching.cache_data_api").setLevel(logging.ERROR)

import assistant  # noqa: E402
import config     # noqa: E402

SUGGESTED = [
    "¿Puede mi hijo asmático jugar en el parque de Getafe esta tarde?",
    "¿A qué hora es mejor salir a correr hoy en Alcalá?",
    "Soy alérgico a las gramíneas, ¿cómo estará el polen estos días?",
]
OTHERS = [
    ("FUERA DE TEMA", "¿Quién ganó el último mundial de fútbol?"),
    ("PREGUNTA MÉDICA", "Tengo asma y hoy me cuesta respirar. ¿Cuántas inhalaciones de salbutamol me tomo?"),
]
# what the panel would send: the chosen place and profile
PANEL = {"lat": 40.4168, "lon": -3.7038, "place": "Madrid (Centro)", "profile": "general", "allergy_types": []}

if not assistant.is_available():
    print("The assistant is not available: ANTHROPIC_API_KEY is missing (or AI_ASSISTANT_ENABLED is False).")
    print("Create a .env file with your key (see the top of this file) and run again.")
    raise SystemExit(1)


def ask(title, question, context):
    result = assistant.answer([{"role": "user", "content": question}], context, visitor="test")
    print(f"\n--- {title}")
    print(f"P: {question}")
    print(f"R: {result['reply']}")
    if result.get("ok"):
        tokens = result["tokens"]
        print(f"   [herramientas: {', '.join(result['tools_used']) or 'ninguna'} | "
              f"{tokens['input']} + {tokens['output']} tokens{' | RESPUESTA CORTADA' if result['cut_short'] else ''}]")
    else:
        print("   [no se generó respuesta]")


print("=" * 78 + "\nEN DIRECTO (datos de ahora)\n" + "=" * 78)
for number, question in enumerate(SUGGESTED, 1):
    ask(f"Pregunta sugerida {number}", question, dict(PANEL, demo=False))

print("\n" + "=" * 78 + f"\nMODO DEMOSTRACIÓN ({config.REPLAY_DATE}, {config.REPLAY_DEFAULT_HOUR}:00)\n" + "=" * 78)
for number, question in enumerate(SUGGESTED, 1):
    ask(f"Pregunta sugerida {number}", question, dict(PANEL, demo=True, hour=config.REPLAY_DEFAULT_HOUR))

print("\n" + "=" * 78 + "\nLÍMITES\n" + "=" * 78)
for title, question in OTHERS:
    ask(title, question, dict(PANEL, demo=False))

print("\nUso total:", assistant.usage_totals())
