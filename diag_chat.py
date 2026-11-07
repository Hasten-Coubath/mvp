"""Diagnóstico rápido do chat no AI Proxy: descobre qual combinação funciona com o seu token."""
import proxy_client as px

msgs = [{"role": "user", "content": "Responda só: ok"}]
testes = [
    ("chat síncrono, latest", lambda: px.chat(msgs, modelo="latest")),
    ("chat síncrono, glm-5.3", lambda: px.chat(msgs, modelo="glm-5.3")),
    ("chat síncrono, glm-5.3 + reasoning_effort", lambda: px.chat(msgs, modelo="glm-5.3", reasoning_effort="minimal")),
    ("chat assíncrono (jobs), latest", lambda: px.chat_job(msgs, modelo="latest", timeout=120)),
    ("chat assíncrono (jobs), glm-5.3 + reasoning_effort", lambda: px.chat_job(msgs, modelo="glm-5.3", timeout=120, reasoning_effort="minimal")),
]
for nome, f in testes:
    try:
        print(f"OK   {nome}: {f()[:40]!r}")
    except Exception as e:
        print(f"ERRO {nome}: {type(e).__name__}: {str(e)[:200]}")
