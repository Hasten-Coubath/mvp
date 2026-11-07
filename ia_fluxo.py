"""IA dentro do fluxo, sempre conferida.

1. Roteamento (Jev): em cada exceção, a IA escolhe QUEM acionar e a URGÊNCIA, lendo o caso e a descrição
   de cada pessoa (politicas.csv, acionar_quando). A escolha é conferida contra a política: se a pessoa não
   está entre as permitidas para aquele tipo de caso, ou a confiança é baixa, vale a regra e o erro da IA
   fica registrado. A IA nunca escolhe aprovador (isso é alçada, definida pela política).
2. Dossiê (chat assíncrono): a IA redige o pedido de aprovação. Conferência antes de usar:
   todo número e todo título citado precisa estar nos fatos enviados; o texto não pode ter dado bancário;
   precisa citar o valor e o alvo; até 700 caracteres. Reprovado -> usa o modelo fixo e registra o erro.
Sem token ou com o proxy fora do ar, o fluxo segue só com as regras.
Limiares fixados antes de rodar.
"""
import re
import pandas as pd

import proxy_client as px
from seguranca import mascarar, checar_saida

CONF_MIN = 0.70
COMPRADOR = {"produto": "PS04", "embalagens": "PS04", "insumos_capa": "PS05", "servicos": "PS05",
             "marketing_agencia": "PS06", "influenciador": "PS06", "frete": "PS07",
             "aluguel": "PS10", "utilidades": "PS10", "ti_saas": "PS11"}


def permitidos(tipo, categoria=None):
    """Quem a política permite acionar em cada tipo de caso."""
    dono = COMPRADOR.get(categoria)
    return {
        "golpe": {"PS08", "PS01", "PS03"},
        "boleto_suspeito": {"PS01", "PS08"},
        "sem_nf": {x for x in (dono, "PS01", "PS04", "PS05") if x},
        "tres_vias": {x for x in (dono, "PS04", "PS05", "PS01") if x},
        "pago_fora": {"PS02", "PS09"},
        "canal_comprometido": {"PS01"},
        "email_ambiguo": {"PS01", "PS08"},
    }.get(tipo, {"PS01"})


class IAFluxo:
    def __init__(self, d):
        po = d["politicas"]
        p = po[po.secao == "acionamento_humano"]
        pessoas = {}
        for _, r in p.iterrows():
            m = re.match(r"pessoas\[(\d+)\]\.(\w+)", r.regra)
            if m:
                pessoas.setdefault(m.group(1), {})[m.group(2)] = r.valor
        self.criterios = {v["pessoa_id"]: f"{v['cargo']}: acionar quando {v['acionar_quando']}"
                          for v in pessoas.values() if "pessoa_id" in v}
        self.registro = []

    # ------------------------------------------------------------ roteamento
    def rotear(self, tipo, motivo, padrao, categoria=None):
        perm = permitidos(tipo, categoria)
        try:
            a = px.jev({"caso": mascarar(motivo), "categoria_do_fornecedor": categoria or ""},
                       {"pessoa": {"type": "choice", "instructions": "Quem na empresa deve ser acionado para este caso?",
                                   "criteria": self.criterios},
                        "urgencia": {"type": "score", "instructions": "Qual a urgência do caso?",
                                     "criteria": ["baixa", "média", "alta"]}})
        except Exception as e:
            self._reg("roteamento", motivo, None, False, f"IA indisponível ({type(e).__name__}: {str(e)[:160]}); regra", padrao)
            return padrao, "normal", "regra"
        escolha, conf = a["pessoa"]["choice"], a["pessoa"]["confidence"]
        urg = ["baixa", "normal", "alta"][min(2, round(a["urgencia"]["score"]))]
        if escolha in perm and conf >= CONF_MIN:
            self._reg("roteamento", motivo, f"{escolha} ({conf:.2f})", True, "dentro da política", escolha)
            return escolha, urg, "ia"
        motivo_rej = "fora da política" if escolha not in perm else f"confiança {conf:.2f} < {CONF_MIN}"
        self._reg("roteamento", motivo, f"{escolha} ({conf:.2f})", False, f"{motivo_rej}; corrigido pela regra", padrao)
        return padrao, urg, "regra (IA corrigida)"

    # ------------------------------------------------------------ dossiê
    @staticmethod
    def conferir_dossie(texto, fatos_txt, alvo_ids, valor_txt):
        prob = []
        if len(texto) > 700:
            prob.append("texto longo demais")
        if checar_saida(texto, None) and any(p for p in checar_saida(texto, None) if not p.startswith("CNPJ")):
            prob.append("dado bancário ou pessoal no texto")
        fatos_num = set(re.findall(r"\d+(?:[.,]\d+)*", fatos_txt))
        fatos_norm = {n.replace(".", "").replace(",", ".") for n in fatos_num} | fatos_num
        for n in re.findall(r"\d+(?:[.,]\d+)*", texto):
            if len(n) >= 3 and n not in fatos_num and n.replace(".", "").replace(",", ".") not in fatos_norm:
                prob.append(f"número que não está nos fatos: {n}")
        for tid in re.findall(r"TP\d+|FP\d+", texto):
            if tid not in fatos_txt:
                prob.append(f"identificador inventado: {tid}")
        if alvo_ids and not any(a in texto for a in alvo_ids):
            prob.append("não cita o título ou fornecedor")
        if valor_txt and valor_txt not in texto.replace(" ", "") and valor_txt.split(",")[0] not in texto:
            prob.append("não cita o valor")
        return prob

    def dossie(self, sol, modelo_fixo):
        fatos = {"acao": sol["acao"], "valor": f"R$ {sol['valor']:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                 "motivo": sol["motivo"], "evidencias": [str(e) for e in sol["evidencias"]][:5],
                 "alvo": {k: v for k, v in sol["params"].items() if k != "titulos"},
                 "aprovadores_exigidos": sol["aprovadores"]}
        fatos_txt = str(fatos)
        alvo_ids = re.findall(r"TP\d+|FP\d+", str(fatos["alvo"]))
        valor_txt = fatos["valor"].replace("R$ ", "") if sol["valor"] else None
        msgs = [{"role": "system", "content": "Você redige pedidos de aprovação para o financeiro, em português, "
                 "em no máximo 4 linhas: o que é, valor, evidência e ação pedida. Use apenas os fatos recebidos; "
                 "não invente números, nomes ou identificadores; cite o título ou fornecedor e o valor exatamente."},
                {"role": "user", "content": fatos_txt}]
        texto, erros = None, []
        for nome, chamar in self.rotas_chat():
            try:
                texto = chamar(msgs).strip()
                self.rota_chat_ok = nome          # guarda a rota que funcionou e usa ela primeiro dali em diante
                break
            except Exception as e:
                erros.append(f"{nome}: {type(e).__name__} {str(e)[:120]}")
        if texto is None:
            self._reg("dossie", sol["id"], None, False, "IA indisponível (" + " | ".join(erros) + "); modelo fixo", modelo_fixo)
            return modelo_fixo, "modelo fixo"
        prob = self.conferir_dossie(texto, fatos_txt, alvo_ids, valor_txt)
        if prob:
            self._reg("dossie", sol["id"], texto, False, "; ".join(prob) + "; trocado pelo modelo fixo", modelo_fixo)
            return modelo_fixo, "modelo fixo (IA reprovada na conferência)"
        self._reg("dossie", sol["id"], texto, True, "aprovado na conferência", texto)
        return texto, "ia"

    def rotas_chat(self):
        """Ordem de tentativa. Sem 'reasoning_effort' (suspeito da falha na rodada real); assíncrono primeiro."""
        rotas = [("chat_job glm-5.3", lambda m: px.chat_job(m, modelo="glm-5.3", timeout=180)),
                 ("chat latest", lambda m: px.chat(m, modelo="latest")),
                 ("chat glm-5.3-flash", lambda m: px.chat(m, modelo="glm-5.3-flash")),
                 ("chat_job latest", lambda m: px.chat_job(m, modelo="latest", timeout=180))]
        ok = getattr(self, "rota_chat_ok", None)
        return sorted(rotas, key=lambda r: r[0] != ok)

    def _reg(self, etapa, entrada, saida_ia, aceita, motivo, usado):
        self.registro.append({"etapa": etapa, "entrada": mascarar(str(entrada))[:200], "saida_ia": mascarar(str(saida_ia))[:400],
                              "aceita": aceita, "conferencia": motivo, "usado": mascarar(str(usado))[:400]})

    def salvar(self, caminho="saida_ia_conferencia.csv"):
        pd.DataFrame(self.registro).to_csv(caminho, index=False)
