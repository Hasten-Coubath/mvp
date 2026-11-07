"""Operação contínua: os agentes acompanham a semana hora a hora (02/11 08:00 em diante).

A cada hora:
  1. revisa os lotes cuja hora de revisão chegou (16:00 do dia útil anterior ao fechamento;
     o lote de 03/11 é revisado logo às 08:00 de 02/11, que é feriado);
  2. confere quem respondeu os pedidos de aprovação (pessoas só respondem em horário comercial);
  3. sem resposta em 2h úteis, escala para o nível de cima (aprovador -> PS01 -> PS03);
  4. fecha os lotes da hora: o que depende de aprovação ainda aberta fica SEGURADO (não sai)
     e volta no próximo lote do mesmo banco quando aprovado.
Tempos de resposta das pessoas são premissas (iguais às de latencia.py), definidas antes de rodar.
Período avaliado pelo parametros.md: até 09/11 08:00. A operação segue até 13/11 para fechar todos os lotes.
"""
import sys
from datetime import datetime, timedelta
import pandas as pd

from livro import carregar
from ambiente import Ambiente
from agentes import Conciliador, Analista
from latencia import Calendario, ACIMA
from teste_cego import custo_atraso

CENARIOS = {
    "normal": {"aprovador": 2, "PS01": 3, "PS03": 4},
    "aprovador_ausente": {"aprovador": None, "PS01": 2, "PS03": 4},
    "ninguem_responde": {"aprovador": None, "PS01": None, "PS03": None},
}
ESCALAR_APOS_H = 2
INICIO, FIM_AVALIACAO, FIM = datetime(2026, 11, 2, 8), datetime(2026, 11, 9, 8), datetime(2026, 11, 13, 18)


def operar(cenario="normal", usar_ia=False, verbose=False):
    d = carregar()
    amb = Ambiente(d)
    amb.assincrono = True
    cal = Calendario()
    resp = CENARIOS[cenario]
    analista = Analista(amb)
    em_verif = {}
    if usar_ia:
        import ia
        from ia_fluxo import IAFluxo
        em_verif, _, log_ia = ia.triagem_emails(d)
        log_ia.to_csv("saida_triagem_ia.csv", index=False)
        amb.ia = IAFluxo(d)
    conc = Conciliador(amb, analista, em_verif)

    ag = d["agenda_remessa"][["lote_remessa", "data_prevista", "horario_fechamento"]].drop_duplicates()
    lotes = []
    for _, l in ag.iterrows():
        fecha = datetime.strptime(f"{l.data_prevista} {l.horario_fechamento}", "%Y-%m-%d %H:%M")
        revisa = max(INICIO, datetime.combine(cal.dia_util_anterior(fecha).date(), datetime.min.time()).replace(hour=16))
        lotes.append({"lote": l.lote_remessa, "fecha": fecha, "revisa": revisa, "revisado": False, "fechado": False})
    lotes.sort(key=lambda x: x["fecha"])

    escalados = {}   # (sol_id, pessoa) -> (substituto, hora em que o substituto responde)
    linha_tempo = []
    t = INICIO
    amb.agora, amb.relogio = t, t.strftime("%Y-%m-%d %H:%M")
    conc.varrer_abertos()
    while t <= FIM:
        amb.agora, amb.relogio = t, t.strftime("%Y-%m-%d %H:%M")
        for L in lotes:                                   # 1. revisões
            if not L["revisado"] and L["revisa"] <= t:
                conc.revisar_lote(L["lote"])
                L["revisado"] = True
        for s in [s for s in amb.solicitacoes if s["decisao"] == "pendente"]:   # 2 e 3. respostas e escalonamento
            criado = s["criada"]
            prazo_esc = cal.somar_horas_uteis(criado, ESCALAR_APOS_H)
            quem = []
            for p in s["aprovadores"]:
                lat = resp.get(p)
                hora = cal.somar_horas_uteis(criado, lat) if lat is not None else None
                if hora is not None and hora <= t:
                    quem.append(p)
                    continue
                if t >= prazo_esc:
                    if (s["id"], p) not in escalados:
                        sub = ACIMA[p]
                        while sub and sub in s["aprovadores"]:
                            sub = ACIMA[sub]
                        if sub:
                            lat_sub = resp.get(sub)
                            hora_sub = cal.somar_horas_uteis(prazo_esc, lat_sub) if lat_sub is not None else None
                            escalados[(s["id"], p)] = (sub, hora_sub)
                            amb.acionar_pessoa("agente_conciliador", "auditor", sub,
                                               f"escalonamento: {p} não respondeu {s['id']} ({s['acao']}) em "
                                               f"{ESCALAR_APOS_H}h úteis", urgencia="alta")
                        else:
                            escalados[(s["id"], p)] = (None, None)
                    sub, hora_sub = escalados[(s["id"], p)]
                    if hora_sub is not None and hora_sub <= t:
                        quem.append(sub)
            if len(quem) == len(s["aprovadores"]):
                amb.aprovar(s["id"], t, quem)
        for L in lotes:                                   # 4. fechamentos
            if not L["fechado"] and L["fecha"] <= t:
                saem = amb.fechar_lote(L["lote"])
                L["fechado"] = True
                linha_tempo.append({"hora": L["fecha"], "lote": L["lote"], "sairam": len(saem),
                                    "segurados_agora": len(amb.segurados),
                                    "pedidos_abertos": sum(s["decisao"] == "pendente" for s in amb.solicitacoes)})
        t += timedelta(hours=1)
    conc.apontar_causas()
    if amb.ia is not None:
        amb.ia.salvar()
    return amb, conc, pd.DataFrame(linha_tempo)


def avaliar(amb, conc):
    d = amb.d
    orig = d["agenda_remessa"].drop_duplicates("titulo_id").set_index("titulo_id")
    dec = pd.DataFrame(conc.decisoes).drop_duplicates("titulo_id", keep="last").set_index("titulo_id")
    pagos = pd.DataFrame(amb.pagos).set_index("titulo_id")
    ruins = set(dec[dec.acao.str.startswith("cancelado")].index)
    indevido = float(pagos[pagos.index.isin(ruins)].valor.sum())
    golpe = float(pagos[pagos.destino.astype(str).str.contains("905/0001", na=False)].valor.sum())
    data_lote = lambda lote: lote[2:10]
    atrasos, custo = [], 0.0
    for tid, r in orig.iterrows():
        if tid in ruins or dec.loc[tid, "acao"].startswith("retido") if tid in dec.index else False:
            continue
        previsto = data_lote(r.lote_remessa)
        if previsto > "20261109":
            continue          # fora do período avaliado
        if tid in pagos.index and data_lote(pagos.loc[tid, "lote"]) <= previsto:
            continue
        v = float(amb.titulos.loc[tid, "v"])
        atrasos.append(tid)
        custo += custo_atraso(v, amb.titulos.loc[tid, "categoria"])
    pedidos = pd.DataFrame(amb.solicitacoes)
    return {"títulos que saíram": len(pagos), "dinheiro indevido (cancelados que saíram)": indevido,
            "dinheiro para a conta do golpe": golpe, "títulos legítimos atrasados (até 09/11)": len(atrasos),
            "custo de atraso (R$)": round(custo, 2),
            "pedidos aprovados": int((pedidos.decisao == "aprovado").sum()), "pedidos ainda abertos": int((pedidos.decisao == "pendente").sum()),
            "escalonamentos": int(sum("escalonamento" in str(a["motivo"]) for a in amb.acionamentos)),
            "acionamentos humanos": len(amb.acionamentos)}


if __name__ == "__main__":
    usar_ia = "--ia" in sys.argv
    linhas = []
    for nome in CENARIOS:
        amb, conc, lt = operar(nome, usar_ia=usar_ia and nome == "normal")
        r = avaliar(amb, conc)
        linhas.append({"cenário": nome, **r})
        if nome == "normal":
            pd.DataFrame(amb.log).to_csv("saida_log_operacao.csv", index=False)
            lt.to_csv("saida_linha_do_tempo.csv", index=False)
            print("Linha do tempo (cenário normal):")
            print(lt.to_string(index=False))
    print("\nResultado por cenário:")
    print(pd.DataFrame(linhas).set_index("cenário").T.to_string())
