"""Painel de autonomia: quanto do trabalho os agentes fizeram sozinhos na operação contínua (cenário normal),
onde e por que uma pessoa entrou, e o que a IA decidiu (se rodado com --ia)."""
import sys
import pandas as pd
from operacao import operar, avaliar

QUEM = {"aprovador": "Cláudia Ferraz (supervisora)", "PS01": "Renata Paiva (coordenadora)", "PS02": "Marcos Teles (tesoureiro)",
        "PS03": "Helena Duarte (diretora)", "PS04": "Carla Menezes (compradora)", "PS05": "Diego Prado (compras)",
        "PS08": "Lívia Campos (jurídico)", "PS10": "Tiago Lins (facilities)", "PS11": "Paula Viana (TI)"}


def main():
    amb, conc, _ = operar("normal", usar_ia="--ia" in sys.argv)
    dec = pd.DataFrame(conc.decisoes)
    log = pd.DataFrame(amb.log)
    pedidos = pd.DataFrame(amb.solicitacoes)
    total = len(dec)
    precisou = set(pedidos.params.map(lambda p: p.get("titulo_id")).dropna())
    for p in pedidos.params:
        precisou |= set(p.get("titulos", []))
    sozinho = total - len(precisou & set(dec.titulo_id))
    print("AUTONOMIA (semana de 02/11 a 13/11, operação hora a hora)\n")
    print(f"Títulos decididos: {total}")
    print(f"  decididos e executados sem nenhuma pessoa: {sozinho} ({sozinho / total:.1%})")
    print(f"  precisaram de aprovação (exigência da política, não do sistema): {total - sozinho}")
    acoes = log[(log.resultado == "ok") & log.ator.str.startswith("agente")].ferramenta.value_counts()
    print("\nAções que os agentes executaram sozinhos:")
    for f, n in acoes.items():
        print(f"  {f}: {n}")
    ac = pd.DataFrame(amb.acionamentos)
    ac["motivo_curto"] = ac.motivo.str.split(":").str[0].str[:45]
    print(f"\nPessoas acionadas ({len(ac)} vezes):")
    for (p, t), g in ac.groupby(["pessoa", "tipo"]):
        print(f"  {QUEM.get(p, p)} [{t}]: {len(g)}  ex.: {g.motivo_curto.iloc[0]}")
    ap = pedidos[pedidos.decisao == "aprovado"]
    if len(ap):
        horas = (pd.to_datetime(ap.aprovada_em) - pd.to_datetime(ap.criada)).dt.total_seconds() / 3600
        print(f"\nPedidos de aprovação: {len(pedidos)}; tempo até a aprovação: mediana {horas.median():.0f}h corridas")
    if "roteado_por" in ac:
        print("\nQuem escolheu a pessoa acionada:", ac.roteado_por.fillna("regra").value_counts().to_dict())
    if "dossie_fonte" in pedidos:
        print("Quem redigiu o pedido de aprovação:", pedidos.dossie_fonte.value_counts().to_dict())
    r = avaliar(amb, conc)
    print(f"\nResultado: dinheiro indevido R$ {r['dinheiro indevido (cancelados que saíram)']:,.2f} | "
          f"atraso R$ {r['custo de atraso (R$)']:,.2f} ({r['títulos legítimos atrasados (até 09/11)']} títulos)")


if __name__ == "__main__":
    main()
