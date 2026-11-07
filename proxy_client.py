"""Cliente do Gogroup AI Proxy (só biblioteca padrão).

Endpoints:
  jev()          POST /v1/systemone            decisão tipada, síncrona (~0,5s)
  oraculo()      POST /v1/oraculo + GET        pergunta sobre dados do Gogroup, assíncrona (20s a 3min)
  goal()         POST /v1/goals + GET          tarefa longa autônoma, assíncrona
  chat()         POST /v1/chat/completions     texto, síncrono
  chat_job()     POST /v1/chat/jobs + GET      texto, assíncrono com long-poll e idempotência

Regras: token só do ambiente/.env (nunca em código ou log); custo de toda chamada é registrado;
deepseek-* é recusado (fora do evento); 'error' não vira loop de nova tentativa.
"""
import json
import os
import time
import uuid
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("AI_PROXY_BASE", "https://ai-proxy.gogroupbr.com/v1")
CUSTOS = []   # uma linha por chamada: {"rota", "modelo", "entrada", "saida", "usd"}


class ProxyErro(Exception):
    def __init__(self, status, corpo):
        super().__init__(f"HTTP {status}: {corpo}")
        self.status, self.corpo = status, corpo


def _token():
    tok = os.environ.get("AI_PROXY_API_KEY")
    if not tok:
        env = Path(__file__).parent / ".env"
        if env.exists():
            for linha in env.read_text().splitlines():
                linha = linha.lstrip("\ufeff").strip()   # .env salvo no Windows (BOM/CRLF)
                if linha.startswith("AI_PROXY_API_KEY="):
                    tok = linha.split("=", 1)[1].strip()
    if not tok:
        raise RuntimeError("AI_PROXY_API_KEY não encontrado (variável de ambiente ou .env)")
    return tok.strip().strip('"').strip("'")


def _http(metodo, rota, corpo=None, headers=None, timeout=60):
    # User-Agent explícito: o padrão "Python-urllib/3.x" costuma ser barrado pelo firewall (403 com corpo vazio/HTML)
    h = {"Authorization": f"Bearer {_token()}", "Content-Type": "application/json", "Accept": "application/json",
         "User-Agent": "gohacks-mvp-pagamentos/1.0", **(headers or {})}
    dados = json.dumps(corpo).encode() if corpo is not None else None
    req = urllib.request.Request(BASE + rota, data=dados, headers=h, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        bruto = e.read().decode("utf-8", "replace")
        try:
            corpo_err = json.loads(bruto)
        except Exception:
            corpo_err = {"corpo_bruto": bruto[:300], "server": e.headers.get("Server"),
                         "cf-ray": e.headers.get("CF-RAY")}
        raise ProxyErro(e.code, corpo_err) from None


def _com_backoff(fn, tentativas=4):
    """429 (cota) e 503 (sobrecarga): espera e tenta de novo. 400 e demais: sobe o erro."""
    for i in range(tentativas):
        try:
            return fn()
        except ProxyErro as e:
            if e.status not in (429, 503) or i == tentativas - 1:
                raise
            time.sleep(min(2 ** i, 20))


def _registrar(rota, modelo, entrada, saida, usd=None):
    CUSTOS.append({"rota": rota, "modelo": modelo, "entrada": entrada, "saida": saida, "usd": usd})
    tot_usd = sum(c["usd"] or 0 for c in CUSTOS)
    tot_tok = sum((c["entrada"] or 0) + (c["saida"] or 0) for c in CUSTOS if c["usd"] is None)
    preco = f"US$ {usd:.7f}" if usd is not None else "sem preço por chamada"
    print(f"💲 {modelo} · entrada {entrada} tok · saída {saida} tok · {preco} "
          f"| sessão: {len(CUSTOS)} chamadas, US$ {tot_usd:.7f} + {tot_tok} tok de chat")


# ------------------------------------------------------------------ Jev
def jev(state, questions):
    """Decisão estruturada. Devolve o dict 'answers'. Use .choice/.score/.noul e 'confidence'."""
    st, hd, r = _com_backoff(lambda: _http("POST", "/systemone", {"state": state, "questions": questions}))
    u = r.get("usage", {})
    usd = u.get("estimated_cost_usd")
    if usd is None and "X-Estimated-Cost-USD" in hd:
        usd = float(hd["X-Estimated-Cost-USD"])
    _registrar("systemone", r.get("model", "jev"), u.get("input_tokens"), u.get("output_tokens"), usd or 0.0)
    return r["answers"]


# ------------------------------------------------------------------ polling genérico
def _poll(rota, intervalo=6, timeout=330, pronto=("done",), erro=("error",)):
    fim = time.time() + timeout
    while time.time() < fim:
        _, _, r = _com_backoff(lambda: _http("GET", rota))
        if r.get("status") in pronto:
            return r
        if r.get("status") in erro:
            raise ProxyErro(200, r.get("error", r))
        time.sleep(intervalo)
    raise TimeoutError(f"{rota} não terminou em {timeout}s")


# ------------------------------------------------------------------ Oráculo
def oraculo(pergunta, timeout=330):
    """Pergunta em português sobre datamart/Painel da Tropa/organograma. Limite 4000 caracteres."""
    if len(pergunta) > 4000:
        raise ValueError("pergunta acima de 4000 caracteres")
    _, _, r = _com_backoff(lambda: _http("POST", "/oraculo", {"question": pergunta}))
    res = _poll(f"/oraculo/{r['id']}", intervalo=6, timeout=timeout)
    _registrar("oraculo", "oraculo", None, res.get("usage", {}).get("total_tokens"))
    return res["answer"]


# ------------------------------------------------------------------ Goal
def goal(objetivo, timeout=900):
    _, _, r = _com_backoff(lambda: _http("POST", "/goals", {"objective": objetivo}))
    res = _poll(f"/goals/{r['id']}", intervalo=8, timeout=timeout)
    _registrar("goals", "goal", None, None)
    return res


# ------------------------------------------------------------------ Chat
def _checar_modelo(modelo):
    if modelo.lower().startswith("deepseek"):
        raise ValueError("deepseek-* está fora do evento; use glm-5.3 (texto) ou kimi-for-coding (código)")


def chat(mensagens, modelo="latest", **extra):
    _checar_modelo(modelo)
    corpo = {"model": modelo, "messages": mensagens, **extra}
    if modelo not in ("latest",) and not modelo.startswith("gpt"):
        corpo.setdefault("max_tokens", 2000)   # modelos extras raciocinam antes de responder
    _, hd, r = _com_backoff(lambda: _http("POST", "/chat/completions", corpo, timeout=180))
    u = r.get("usage", {})
    _registrar("chat", hd.get("x-model-served", r.get("model")), u.get("prompt_tokens"), u.get("completion_tokens"))
    return r["choices"][0]["message"]["content"]


def chat_job(mensagens, modelo="latest", timeout=660, **extra):
    """Assíncrono: sobrevive a plataformas que cortam requisição longa. Reenvio usa a MESMA chave."""
    _checar_modelo(modelo)
    corpo = {"model": modelo, "messages": mensagens, **extra}
    corpo.pop("stream", None)
    if modelo not in ("latest",) and not modelo.startswith("gpt"):
        corpo.setdefault("max_tokens", 2000)
    chave = str(uuid.uuid4())
    for tentativa in range(3):
        _, _, r = _com_backoff(lambda: _http("POST", "/chat/jobs", corpo, headers={"Idempotency-Key": chave}))
        fim = time.time() + timeout
        while time.time() < fim:
            _, _, j = _com_backoff(lambda: _http("GET", f"/chat/jobs/{r['id']}?wait=25", timeout=40))
            if j["status"] == "done":
                c = j["completion"]
                u = c.get("usage", {})
                _registrar("chat_job", c.get("model"), u.get("prompt_tokens"), u.get("completion_tokens"))
                return c["choices"][0]["message"]["content"]
            if j["status"] == "error":
                if j.get("error", {}).get("retryable") and tentativa < 2:
                    break                      # reenvia o MESMO POST com a MESMA chave
                raise ProxyErro(200, j.get("error"))
        else:
            raise TimeoutError("chat job não terminou")
    raise ProxyErro(200, "chat job falhou após 3 tentativas")
