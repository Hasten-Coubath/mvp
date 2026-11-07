"""Segurança do agente: nem dinheiro nem dado saem sem motivo.

1. canal_confiavel  -> o agente só fala com o fornecedor por um canal (e-mail/telefone) cuja última troca
                       foi confirmada. Canal trocado sem confirmação pode ser do golpista.
2. checar_saida     -> toda mensagem para fora passa por aqui: bloqueia dados bancários (linha digitável,
                       conta, chave pix), CPF e CNPJ de terceiros. Mensagem para fornecedor não leva
                       dado de outro fornecedor nem dado bancário de ninguém.
3. mascarar         -> logs, pedidos de aprovação, avisos internos e o que vai para a IA carregam os
                       números mascarados (quem decide vê o suficiente para decidir, não o dado inteiro).
Os controles ficam na camada de ferramentas: o agente não consegue contorná-los.
"""
import re
import pandas as pd

RAIZ_GOGROUP = "41208533"

_LD = re.compile(r"(?<!\d)\d{47}(?!\d)")
_LD_FMT = re.compile(r"\d{5}\.\d{5}\s\d{5}\.\d{6}\s\d{5}\.\d{6}\s\d\s\d{14}")
_CONTA = re.compile(r"(?<!\d)\d{7}-\d(?!\d)")
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_CPF = re.compile(r"(?<!\d)\d{3}\.\d{3}\.\d{3}-\d{2}(?!\d)")
# padrões com contexto: o número só é sensível quando vem depois da palavra que o define
_CTX_CONTA = re.compile(r"(?i)\b(conta(?:\s+corrente)?|c/c|cc)\b[\s:nº°.]*((?<!\d)\d[\d.]{3,13}(?:-\d)?(?!\d))")
_CTX_AG = re.compile(r"(?i)\b(ag[eê]ncia|ag\.?)\s*[:nº°]*\s*((?<!\d)\d{3,5}(?:-\d)?(?!\d))")
_CTX_PIX = re.compile(r"(?i)\b((?:chave\s+)?pix)\b[\s:]*(?:é\s+|e\s+)?([^\s,;]+@[^\s,;]+\.[a-z]{2,}|\(?\d{2}\)?\s?\d{4,5}-?\d{4}|\d{11,14})")
_CTX_CPF = re.compile(r"(?i)\b(cpf)\b[\s:nº°]*(\d{11})(?!\d)")
_CONTEXTUAIS = [("número de conta", _CTX_CONTA), ("agência", _CTX_AG), ("chave pix", _CTX_PIX), ("CPF", _CTX_CPF)]
_CNPJ = re.compile(r"(?<!\d)(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}|\d{14})(?!\d)")


def mascarar(texto):
    if texto is None or (isinstance(texto, float) and pd.isna(texto)):
        return texto
    if isinstance(texto, (list, tuple)):
        return [mascarar(x) for x in texto]
    s = str(texto)
    s = _LD.sub(lambda m: f"[linha digitável …{m.group(0)[-4:]}]", s)
    s = _LD_FMT.sub("[linha digitável]", s)
    s = _CONTA.sub(lambda m: f"***{m.group(0)[-5:]}", s)
    s = _UUID.sub("[chave pix]", s)
    s = _CPF.sub("[CPF]", s)
    for _, rx in _CONTEXTUAIS:
        s = rx.sub(lambda m: f"{m.group(1)} [oculto]", s)
    return s


def checar_saida(texto, raiz_destinatario=None):
    """Devolve a lista de problemas; vazia = pode sair."""
    s = str(texto)
    prob = []
    if _LD.search(s) or _LD_FMT.search(s):
        prob.append("linha digitável")
    if _CONTA.search(s):
        prob.append("número de conta")
    if _UUID.search(s):
        prob.append("chave pix")
    if _CPF.search(s):
        prob.append("CPF")
    for nome, rx in _CONTEXTUAIS:
        if rx.search(s) and nome not in prob:
            prob.append(nome)
    for c in _CNPJ.findall(s):
        raiz = re.sub(r"\D", "", c)[:8]
        if raiz not in {raiz_destinatario, RAIZ_GOGROUP}:
            prob.append(f"CNPJ de terceiro ({c})")
    return prob


def canal_confiavel(d, fid):
    """Devolve (canal, motivo). canal: 'email', 'telefone' ou None (nenhum canal confiável)."""
    ac = d["alteracoes_cadastro"]
    motivos = []
    for campo in ("email", "telefone"):
        tr = ac[(ac.fornecedor_id == fid) & (ac.campo == campo)].sort_values("data_hora")
        if tr.empty:
            return campo, f"{campo} original do cadastro"
        ult = tr.iloc[-1]
        if ult.confirmacao_registrada == "sim":
            return campo, f"{campo} trocado em {ult.data_hora} com confirmação"
        motivos.append(f"{campo} trocado em {ult.data_hora} via {ult.origem} sem confirmação ({ult.alteracao_id})")
    return None, "; ".join(motivos)
