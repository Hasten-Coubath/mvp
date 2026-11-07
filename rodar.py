"""Roda os lotes em ordem de fechamento: Conciliador revisa -> Analista resolve -> tesoureiro libera."""
import sys
import pandas as pd
from livro import carregar
from ambiente import Ambiente
from agentes import Conciliador, Analista


def rodar(d, verbose=True, usar_ia=False):
    amb = Ambiente(d)
    analista = Analista(amb)
    em_verif = {}
    if usar_ia:
        import ia
        em_verif, para_humano, log_ia = ia.triagem_emails(d)
        log_ia.to_csv("saida_triagem_ia.csv", index=False)
        from ia_fluxo import IAFluxo
        amb.ia = IAFluxo(d)
        for cid in para_humano:
            amb.acionar_pessoa("agente_conciliador", "auditor", "PS01", f"e-mail {cid} ambíguo para a IA",
                               tipo="email_ambiguo")
    conc = Conciliador(amb, analista, em_verif)
    conc.varrer_abertos()
    ag = d["agenda_remessa"][["lote_remessa", "data_prevista", "horario_fechamento"]].drop_duplicates()
    ag = ag.sort_values(["data_prevista", "horario_fechamento"])
    for _, l in ag.iterrows():
        amb.relogio = f"{l.data_prevista} {l.horario_fechamento}"
        conc.revisar_lote(l.lote_remessa)
        amb.fechar_lote(l.lote_remessa)
    conc.apontar_causas()
    if amb.ia is not None:
        amb.ia.salvar()
    return amb, conc


if __name__ == "__main__":
    d = carregar()
    amb, conc = rodar(d, usar_ia="--ia" in sys.argv)
    dec = pd.DataFrame(conc.decisoes)
    print(dec.groupby("acao").agg(n=("valor", "size"), rs=("valor", "sum")).round(2))
    pagos = pd.DataFrame(amb.pagos)
    print(f"\nSaiu: {len(pagos)} títulos, R$ {pagos.valor.sum():,.2f}")
    print(f"Acionamentos humanos: {len(amb.acionamentos)}  Solicitações: {len(amb.solicitacoes)}  "
          f"Incidentes: {len(amb.incidentes)}")
    pd.DataFrame(amb.log).to_csv("saida_log_acoes.csv", index=False)
    dec.to_csv("saida_decisoes.csv", index=False)
