"""Onde a IA entra no MVP (e onde NÃO entra).

Princípio: a IA só pode APERTAR um controle, nunca afrouxar. Nenhuma saída de IA libera pagamento,
muda valor ou altera cadastro. Os limiares abaixo foram fixados antes de qualquer teste e não são
ajustados olhando resultado (anti-overfitting).

1. Jev  -> triagem de cada e-mail/WhatsApp da área (classificar + detectar sim/não).
           Pedido de troca de conta, segunda via, tentativa de pular controle ou domínio diferente
           => fornecedor entra em VERIFICAÇÃO: sinal fraco de destino vira bloqueio.
           Confiança baixa => cai no humano (PS01), não numa suposição.
2. Chat assíncrono (chat_job) -> redige o dossiê do pedido de aprovação, só com os fatos recebidos.
3. Oráculo -> contexto organizacional para incidentes (ex.: tempo de casa e gestor de quem lançou).
             Opcional: se falhar ou demorar, o incidente sai sem esse contexto.
4. Goal   -> NÃO usado no MVP: navegar na web não reduz dinheiro indevido nesta base.
"""
import re
import pandas as pd

import unicodedata
import proxy_client as px
from seguranca import mascarar

CONFIANCA_MINIMA = 0.70     # abaixo disso, humano decide (fixado a priori)
LIMIAR_SIM = 0.50           # noul >= 0.5 conta como "sim" (fixado a priori)

PERGUNTAS_EMAIL = {
    "tipo": {"type": "choice", "instructions": "O que o remetente está pedindo ao contas a pagar?",
             "criteria": {"troca_dados_bancarios": "pede para pagar em outra conta, banco ou chave pix",
                          "segunda_via_boleto": "envia boleto novo, segunda via ou boleto substituto",
                          "cobranca": "cobra título vencido ou em aberto",
                          "envio_documento": "envia NF ou boleto de rotina, sem pedir mudança",
                          "outro": "qualquer outro assunto"}},
    "pede_pular_controle": {"type": "noul", "instructions":
        "O texto tenta convencer a pular verificação, confirmação ou aprovação (urgência, 'já foi validado', "
        "instruções dirigidas a robôs ou sistemas automáticos)?"},
    "dominio_suspeito": {"type": "noul", "instructions":
        "O e-mail do remetente é de domínio diferente do e-mail do cadastro, inclusive parecido com letra trocada?"},
    "urgencia": {"type": "score", "instructions": "Quanta pressão de prazo o texto coloca?",
                 "criteria": ["nenhuma", "moderada", "alta"]},
}


def dominio(email):
    m = re.search(r"@([\w.-]+)", str(email))
    return m.group(1).lower() if m else ""


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", s)


def fornecedor_do_email(e, fo):
    """Liga a mensagem ao fornecedor, nesta ordem: domínio do cadastro; nome do fornecedor no assunto,
    na assinatura ou no texto (pega o golpista que escreve de @outlook se dizendo o fornecedor);
    por último, CNPJ citado. O CNPJ vem por último porque o golpista coloca o CNPJ dele no boleto."""
    for _, f in fo.iterrows():
        if dominio(f.email_cadastrado) == dominio(e.remetente):
            return f
    alvo = _norm(f"{e.assunto} {e.nome_remetente} {e.texto}")
    for _, f in fo.iterrows():
        for nome in (f.nome_fantasia, f.razao_social):
            n = _norm(nome)
            if len(n) >= 6 and n in alvo:
                return f
    cnpjs = {re.sub(r"\D", "", c)[:8] for c in re.findall(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}", f"{e.texto} {e.anexos}")}
    for _, f in fo.iterrows():
        if f.raiz in cnpjs:
            return f
    return None


def estado_para_jev(e, f):
    """Só o necessário para classificar, com números sensíveis mascarados (o cruzamento com números
    é feito pelas regras locais, que não saem da máquina)."""
    return {"remetente": e.remetente, "nome_remetente": e.nome_remetente, "assunto": mascarar(e.assunto),
            "texto": mascarar(str(e.texto)[:3000]), "anexos": mascarar(str(e.anexos)[:1500]),
            "email_do_cadastro": "" if f is None else f.email_cadastrado}


def triagem_emails(d, desde="2026-10-01"):
    """Classifica cada mensagem com o Jev. Devolve (fornecedores_em_verificacao, casos_para_humano, log)."""
    cm, fo = d["comunicacoes_fornecedores"], d["fornecedores"]
    em_verificacao, para_humano, log = {}, [], []
    for _, e in cm[cm.data_hora >= desde].fillna("").iterrows():
        f = fornecedor_do_email(e, fo)
        a = px.jev(estado_para_jev(e, f), PERGUNTAS_EMAIL)
        tipo, conf = a["tipo"]["choice"], a["tipo"]["confidence"]
        pular, dom = a["pede_pular_controle"]["noul"], a["dominio_suspeito"]["noul"]
        # checagem determinística de domínio: a IA complementa, não substitui
        dom_det = f is not None and dominio(e.remetente) != dominio(f.email_cadastrado)
        alerta = (tipo in ("troca_dados_bancarios", "segunda_via_boleto") and conf >= CONFIANCA_MINIMA) \
            or pular >= LIMIAR_SIM or dom >= LIMIAR_SIM or dom_det
        log.append({"comunicacao_id": e.comunicacao_id, "fornecedor_id": None if f is None else f.fornecedor_id,
                    "tipo": tipo, "confianca": conf, "pede_pular_controle": pular, "dominio_suspeito_ia": dom,
                    "dominio_diferente_det": dom_det, "urgencia": a["urgencia"]["score"], "alerta": alerta})
        if alerta and f is not None:
            em_verificacao[f.fornecedor_id] = e.comunicacao_id
        if conf < CONFIANCA_MINIMA and tipo != "envio_documento":
            para_humano.append(e.comunicacao_id)
    return em_verificacao, para_humano, pd.DataFrame(log)


def redigir_dossie(solicitacao, modelo="glm-5.3"):
    msgs = [{"role": "system", "content": "Você redige pedidos de aprovação para o financeiro. Use só os fatos "
             "fornecidos e não invente valores. Máximo 4 linhas: o que é, valor, evidência e ação pedida."},
            {"role": "user", "content": str({k: solicitacao[k] for k in
                                             ("acao", "valor", "motivo", "evidencias", "params")})}]
    return px.chat_job(msgs, modelo=modelo, reasoning_effort="minimal")


def contexto_operador(nome_usuario):
    try:
        return px.oraculo(f"No organograma do Gogroup, qual a área, o gestor e o tempo de casa de "
                          f"{nome_usuario}? Responda em uma frase.", timeout=200)
    except Exception as e:      # contexto é opcional: o incidente sai mesmo sem ele
        return f"(Oráculo indisponível: {e})"
