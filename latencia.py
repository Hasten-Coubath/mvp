"""E se ninguém aprovar a tempo? Simula o tempo de resposta das pessoas sobre as decisões reais dos agentes.

Regras (definidas ANTES de rodar, não ajustadas depois):
- O agente revisa cada lote às 16:00 do dia útil anterior (dá tempo de as pessoas responderem).
- Pessoas só respondem em horário comercial (08:00–18:00, dias úteis; feriados de feriados_bancarios.csv).
- Padrão seguro: sem aprovação, NADA que dependa de aprovação acontece. O título continua fora do lote.
  Logo, falta de resposta nunca vira perda; vira, no máximo, custo de atraso.
- Lembrete e escalonamento: 1 hora antes do fechamento, se o pedido segue aberto, o agente escala
  para o nível de cima (aprovador -> coordenadora PS01 -> diretora PS03).
- Cancelar duplicidade sem aprovação não custa nada (o título duplicado já está fora do lote).
"""
from datetime import datetime, timedelta
import pandas as pd

from livro import carregar
from rodar import rodar
from teste_cego import custo_atraso

CENARIOS = {   # horas úteis até responder; None = não responde
    "1. Todos respondem em 30 min": {"aprovador": 0.5, "PS01": 0.5, "PS03": 0.5, "escalar": True},
    "2. Dia normal (2h / 3h / 4h)": {"aprovador": 2, "PS01": 3, "PS03": 4, "escalar": True},
    "3. Aprovador ausente, com escalonamento": {"aprovador": None, "PS01": 2, "PS03": 4, "escalar": True},
    "4. Aprovador ausente, sem escalonamento": {"aprovador": None, "PS01": 2, "PS03": 4, "escalar": False},
    "5. Ninguém responde": {"aprovador": None, "PS01": None, "PS03": None, "escalar": True},
    # v2 do escalonamento (depois de ver o 3 falhar): escala se não houver resposta em 2h úteis
    "3b. Aprovador ausente, escala após 2h úteis": {"aprovador": None, "PS01": 2, "PS03": 4, "escalar": True, "escalar_apos_h": 2},
    "2b. Dia normal, escala após 2h úteis": {"aprovador": 2, "PS01": 3, "PS03": 4, "escalar": True, "escalar_apos_h": 2},
}
ACIMA = {"aprovador": "PS01", "PS01": "PS03", "PS03": None}


class Calendario:
    def __init__(self):
        f = pd.read_csv("dados/feriados_bancarios.csv", dtype=str)
        self.feriados = set(f[f.sem_expediente_bancario == "sim"].data)

    def util(self, dia):
        return dia.weekday() < 5 and dia.strftime("%Y-%m-%d") not in self.feriados

    def dia_util_anterior(self, dia):
        d = dia - timedelta(days=1)
        while not self.util(d):
            d -= timedelta(days=1)
        return d

    def somar_horas_uteis(self, inicio, horas):
        """Avança 'horas' dentro de 08:00–18:00 de dias úteis."""
        t, resta = inicio, timedelta(hours=horas)
        while True:
            if not self.util(t) or t.hour >= 18:
                t = datetime.combine((t + timedelta(days=1)).date(), datetime.min.time()).replace(hour=8)
                continue
            if t.hour < 8:
                t = t.replace(hour=8, minute=0)
            fim_dia = t.replace(hour=18, minute=0)
            if t + resta <= fim_dia:
                return t + resta
            resta -= fim_dia - t
            t = fim_dia


def simular(amb, conc, cenario, cal):
    ag = amb.d["agenda_remessa"].drop_duplicates("titulo_id").set_index("titulo_id")
    fecha = lambda tid: datetime.strptime(f"{ag.loc[tid, 'data_prevista']} {ag.loc[tid, 'horario_fechamento']}", "%Y-%m-%d %H:%M")
    linhas, acionamentos_extra = [], 0
    for s in amb.solicitacoes:
        p = s["params"]
        if "titulos" in p:      # cancelamento em lote de duplicatas em aberto: sem lote, sem custo de atraso
            continue
        if "titulo_id" in p:
            afetados = [p["titulo_id"]]
        else:   # reversão de cadastro: segura todos os TED/PIX do fornecedor até aprovar
            afetados = [t for t in ag.index if ag.loc[t, "fornecedor_id"] == p["fornecedor_id"]
                        and ag.loc[t, "forma_pagamento"] in ("ted", "pix")]
        primeiro_corte = min(fecha(t) for t in afetados)
        criado = datetime.combine(cal.dia_util_anterior(primeiro_corte).date(), datetime.min.time()).replace(hour=16)
        aprovado_em = criado
        for pessoa in s["aprovadores"]:          # aprovação dupla: precisa de todos
            lat = cenario[pessoa]
            t = cal.somar_horas_uteis(criado, lat) if lat is not None else None
            esc_em = (cal.somar_horas_uteis(criado, cenario["escalar_apos_h"]) if cenario.get("escalar_apos_h")
                      else primeiro_corte - timedelta(hours=1))
            if (t is None or t > esc_em) and cenario["escalar"]:
                sub = ACIMA[pessoa]
                while sub and sub in s["aprovadores"]:
                    sub = ACIMA[sub]                 # não pode ser a mesma pessoa da dupla
                if sub and cenario[sub] is not None:
                    acionamentos_extra += 1
                    t2 = cal.somar_horas_uteis(esc_em, cenario[sub])
                    t = t2 if t is None else min(t, t2)
            aprovado_em = None if (t is None or aprovado_em is None) else max(aprovado_em, t)
        for tid in afetados:
            atrasou = aprovado_em is None or aprovado_em > fecha(tid)
            custo = 0.0
            if atrasou and s["acao"] != "cancelar_titulo":
                custo = custo_atraso(float(amb.titulos.loc[tid, "v"]), amb.titulos.loc[tid, "categoria"])
            linhas.append({"solicitacao": s["id"], "acao": s["acao"], "titulo_id": tid,
                           "aprovado_em": aprovado_em, "fechamento": fecha(tid), "atrasou": atrasou, "custo_atraso": custo})
    return pd.DataFrame(linhas), acionamentos_extra


if __name__ == "__main__":
    amb, conc = rodar(carregar())
    cal = Calendario()
    print(f"{len(amb.solicitacoes)} pedidos de aprovação reais do MVP\n")
    res = []
    for nome, c in CENARIOS.items():
        df, extra = simular(amb, conc, c, cal)
        sol = df.drop_duplicates("solicitacao")
        res.append({"cenário": nome, "pedidos respondidos a tempo": f"{(~df.groupby('solicitacao').atrasou.any()).sum()}/{len(sol)}",
                    "títulos legítimos atrasados": int(((df.atrasou) & (df.custo_atraso > 0)).sum()),
                    "custo de atraso (R$)": round(df.custo_atraso.sum(), 2),
                    "escalonamentos": extra, "dinheiro indevido (R$)": 0.0})
        df.to_csv(f"saida_latencia_{nome[0]}.csv", index=False)
    print(pd.DataFrame(res).to_string(index=False))
