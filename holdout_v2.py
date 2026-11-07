"""HOLDOUT v2 (selado). Escrito ANTES de implementar a conferência de 3 vias, a partir do texto da
política (politicas.csv / parametros.md), não do código. Regra do time: rodar uma vez depois da
implementação e reportar o resultado como sair, sem ajustar o sistema com base nele.
O hash SHA-256 deste arquivo é registrado antes da implementação.
"""
import copy
import random
import pandas as pd

from livro import carregar, Livro
from rodar import rodar
from teste_cego import custo_atraso


def injetar(d, seed):
    rnd = random.Random(seed)
    d = copy.deepcopy(d)
    cp, ag, nf = d["contas_a_pagar"], d["agenda_remessa"], d["notas_fiscais"]
    pc = d.setdefault("pedidos_compra", pd.read_csv("dados/pedidos_compra.csv", dtype=str))
    amb0, conc0 = rodar(carregar())
    exc = {x["titulo_id"] for x in conc0.decisoes if x["acao"] != "segue"}
    L0 = Livro(carregar())
    fila = cp[cp.titulo_id.isin(ag.titulo_id) & ~cp.titulo_id.isin(exc)]
    fila = fila[[L0.avaliar(t)[0] == "OK" for _, t in fila.iterrows()]]
    oc_ok = set(pc[(pc.status == "aprovada") & (pc.tipo == "pontual")].oc_id)
    usados, gab = set(), []

    def escolher(f):
        c = fila[f(fila) & ~fila.titulo_id.isin(usados)]
        t = c.sample(1, random_state=rnd.randint(0, 10**6)).iloc[0]
        usados.add(t.titulo_id)
        return t

    def mnf(t):
        return (nf.cnpj_emitente.str[:8] == t.raiz) & (nf.numero_nf.str.lstrip("0") == t.nfn)

    com_oc = lambda f: f.oc_id.isin(oc_ok) & (f.nfn != "")

    t = escolher(com_oc); nf.loc[mnf(t), "situacao_sefaz"] = "cancelada"
    gab.append((t.titulo_id, "H1_nf_cancelada_sefaz", "erro", t.v))
    t = escolher(com_oc); nf.loc[mnf(t), "situacao_sefaz"] = "denegada"
    gab.append((t.titulo_id, "H2_nf_denegada_sefaz", "erro", t.v))
    t = escolher(com_oc); pc.loc[pc.oc_id == t.oc_id, "status"] = "cancelada"
    gab.append((t.titulo_id, "H3_oc_cancelada", "erro", t.v))
    t = escolher(com_oc); pc.loc[pc.oc_id == t.oc_id, "status"] = "pendente_aprovacao"
    gab.append((t.titulo_id, "H4_oc_pendente_aprovacao", "legitimo_com_aprovacao_dupla", t.v))

    def oc_para(t, fator):
        tot = nf.loc[mnf(t), "valor_total"].astype(float).iloc[0]
        pc.loc[pc.oc_id == t.oc_id, "valor_total"] = f"{tot / fator:.2f}"
        return tot

    t = escolher(lambda f: com_oc(f) & (f.parcela == "1/1") & (f.v > 5000)); tot = oc_para(t, 1.10)
    gab.append((t.titulo_id, "H5_nf_10pct_acima_da_oc", "erro_parcial", round(tot - tot / 1.10, 2)))
    t = escolher(lambda f: com_oc(f) & (f.parcela == "1/1") & (f.v > 5000)); oc_para(t, 1.015)
    gab.append((t.titulo_id, "H6_nf_1_5pct_acima_da_oc", "legitimo", 0))
    t = escolher(lambda f: com_oc(f) & (f.parcela == "1/1") & (f.v < 2000) & (f.v > 600))
    tot = nf.loc[mnf(t), "valor_total"].astype(float).iloc[0]
    pc.loc[pc.oc_id == t.oc_id, "valor_total"] = f"{tot - 45:.2f}"
    gab.append((t.titulo_id, "H7_nf_45_reais_acima_oc_pequena", "legitimo", 0))
    t = escolher(lambda f: f.categoria == "utilidades")
    cp.loc[cp.titulo_id == t.titulo_id, "oc_id"] = None
    gab.append((t.titulo_id, "H8_utilidade_sem_oc", "legitimo", 0))

    # H9 duplicata do mesmo título em outro banco pagador no mesmo dia
    t = escolher(com_oc)
    nt = t.copy(); nt["titulo_id"] = "TP950001"; nt["origem_lancamento"] = "manual"
    nt["data_lancamento"] = "2026-11-02 09:00:00"
    a = ag[ag.titulo_id == t.titulo_id].iloc[0].copy()
    outro = ag[(ag.data_prevista == a.data_prevista) & (ag.lote_remessa != a.lote_remessa)]
    if len(outro):
        a["lote_remessa"], a["banco_pagador"], a["horario_fechamento"] = outro.iloc[0][["lote_remessa", "banco_pagador", "horario_fechamento"]]
    a["titulo_id"] = nt.titulo_id
    cp.loc[len(cp)] = nt
    ag.loc[len(ag)] = a
    gab.append((nt.titulo_id, "H9_duplicata_em_outro_banco", "erro", t.v))

    # H10 NF citada existe, mas é de outro fornecedor (raiz diferente)
    t = escolher(com_oc)
    outra = nf[(nf.cnpj_emitente.str[:8] != t.raiz)].sample(1, random_state=rnd.randint(0, 999)).iloc[0]
    cp.loc[cp.titulo_id == t.titulo_id, ["numero_nf_digitado", "nfn"]] = [outra.numero_nf, outra.numero_nf.lstrip("0")]
    gab.append((t.titulo_id, "H10_nf_de_outro_fornecedor", "erro", t.v))

    # H11 telefone trocado há 45 dias (não é recente) e conta trocada confirmada nesse telefone: legítimo
    t = escolher(lambda f: f.forma_pagamento == "ted")
    fo, ac = d["fornecedores"], d["alteracoes_cadastro"]
    f = fo[fo.fornecedor_id == t.fornecedor_id].iloc[0]
    ac.loc[len(ac)] = {"alteracao_id": "AC77001", "data_hora": "2026-09-16 10:00:00", "fornecedor_id": f.fornecedor_id,
                       "campo": "telefone", "valor_anterior": f.telefone_cadastrado, "valor_novo": "(85) 3222-1111",
                       "alterado_por": "usuario_03", "origem": "portal_fornecedor", "confirmacao_registrada": "sim",
                       "confirmacao_como": "confirmado por e-mail do domínio cadastrado"}
    for campo, novo in [("banco", "289"), ("agencia", "1234"), ("conta", "7654321-0")]:
        ac.loc[len(ac)] = {"alteracao_id": f"AC7710{campo[0]}", "data_hora": "2026-10-31 10:00:00",
                           "fornecedor_id": f.fornecedor_id, "campo": campo, "valor_anterior": f[campo],
                           "valor_novo": novo, "alterado_por": "usuario_03", "origem": "portal_fornecedor",
                           "confirmacao_registrada": "sim",
                           "confirmacao_como": "ligação para o telefone do cadastro (85) 3222-1111 em 31/10/2026; confirmado"}
    fo.loc[fo.fornecedor_id == f.fornecedor_id, ["telefone_cadastrado", "banco", "agencia", "conta"]] = \
        ["(85) 3222-1111", "289", "1234", "7654321-0"]
    for _, x in fila[(fila.fornecedor_id == f.fornecedor_id) & (fila.forma_pagamento == "ted")].iterrows():
        usados.add(x.titulo_id)
        gab.append((x.titulo_id, "H11_troca_confirmada_telefone_antigo_45d", "legitimo", 0))

    return d, pd.DataFrame(gab, columns=["titulo_id", "tipo", "natureza", "valor_em_risco"])


def avaliar(seeds=(11, 29, 47, 83)):
    linhas = []
    for seed in seeds:
        d, g = injetar(carregar(), seed)
        amb, conc = rodar(d)
        pagos = {p["titulo_id"]: p["valor"] for p in amb.pagos}
        tit = amb.titulos
        for _, r in g.iterrows():
            saiu = r.titulo_id in pagos
            perda = atraso = 0.0
            if r.natureza == "erro" and saiu:
                perda = pagos[r.titulo_id]
            elif r.natureza == "erro_parcial":
                if saiu:
                    perda = max(0.0, r.valor_em_risco - (tit.loc[r.titulo_id, "v"] - pagos[r.titulo_id]))
            elif r.natureza.startswith("legitimo") and not saiu:
                atraso = custo_atraso(float(tit.loc[r.titulo_id, "v"]), tit.loc[r.titulo_id, "categoria"])
            linhas.append({**r.to_dict(), "seed": seed, "saiu": saiu, "perda": round(perda, 2),
                           "atraso": round(atraso, 2)})
    R = pd.DataFrame(linhas)
    R.to_csv("saida_holdout_v2.csv", index=False)
    return R


if __name__ == "__main__":
    R = avaliar()
    print(R.groupby(["tipo", "natureza"]).agg(casos=("saiu", "size"), sairam=("saiu", "sum"),
                                              perda=("perda", "sum"), atraso=("atraso", "sum")).round(0).to_string())
    print(f"\nTOTAL perda R$ {R.perda.sum():,.0f} | atraso R$ {R.atraso.sum():,.0f}")
