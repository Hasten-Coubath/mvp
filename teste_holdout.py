"""Holdout: tipos de erro NOVOS, escritos depois do teste cego e rodados UMA vez, sem ajuste.

O teste cego (teste_cego.py) ficou contaminado: corrigi o sistema depois de ver as falhas (E8, E9, E11).
Este arquivo existe para medir o que o sistema faz com o que ele nunca viu.
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
    cp, ag, fo, nf = d["contas_a_pagar"], d["agenda_remessa"], d["fornecedores"], d["notas_fiscais"]
    pc = pd.read_csv("dados/pedidos_compra.csv", dtype=str)
    amb0, conc0 = rodar(carregar())
    exc = {x["titulo_id"] for x in conc0.decisoes if x["acao"] != "segue"}
    L0 = Livro(carregar())
    fila = cp[cp.titulo_id.isin(ag.titulo_id) & ~cp.titulo_id.isin(exc)]
    fila = fila[[L0.avaliar(t)[0] == "OK" for _, t in fila.iterrows()]]
    usados, gab = set(), []

    def escolher(f=None):
        c = fila if f is None else fila[f(fila)]
        c = c[~c.titulo_id.isin(usados)]
        t = c.sample(1, random_state=rnd.randint(0, 10**6)).iloc[0]
        usados.add(t.titulo_id)
        return t

    def nf_do(t):
        return (nf.cnpj_emitente.str[:8] == t.raiz) & (nf.numero_nf.str.lstrip("0") == t.nfn)

    for _ in range(2):   # N1 título lançado no fornecedor errado (o dinheiro iria para outra empresa)
        t = escolher(lambda f: f.nfn != "")
        outro = fo[(fo.categoria == t.categoria) & (fo.fornecedor_id != t.fornecedor_id)].sample(1, random_state=rnd.randint(0, 999)).iloc[0]
        cp.loc[cp.titulo_id == t.titulo_id, ["fornecedor_id", "raiz"]] = [outro.fornecedor_id, outro.raiz]
        gab.append((t.titulo_id, "N1_fornecedor_errado", "erro", t.v))
    for _ in range(2):   # N2 NF cancelada na SEFAZ depois de lançada
        t = escolher(lambda f: f.nfn != "")
        nf.loc[nf_do(t), "situacao_sefaz"] = "cancelada"
        gab.append((t.titulo_id, "N2_nf_cancelada_sefaz", "erro", t.v))
    for _ in range(2):   # N3 OC cancelada em Compras (sem compra válida)
        t = escolher(lambda f: f.oc_id.notna() & (f.categoria != "utilidades"))
        pc.loc[pc.oc_id == t.oc_id, "status"] = "cancelada"
        gab.append((t.titulo_id, "N3_oc_cancelada", "erro", t.v))
    for _ in range(2):   # N4 NF 15% acima da OC (fornecedor cobrou a mais; título bate com a NF)
        t = escolher(lambda f: f.oc_id.notna() & (f.categoria.isin(["produto", "embalagens", "insumos_capa"])))
        pc.loc[pc.oc_id == t.oc_id, "valor_total"] = (pc.loc[pc.oc_id == t.oc_id, "valor_total"].astype(float) / 1.15).round(2).astype(str)
        gab.append((t.titulo_id, "N4_nf_acima_da_oc", "erro_parcial", round(t.v * 0.15 / 1.15, 2)))
    for _ in range(2):   # N5 chave PIX trocada sem confirmação (só o campo chave_pix)
        t = escolher(lambda f: f.forma_pagamento == "pix")
        ac = d["alteracoes_cadastro"]
        ac.loc[len(ac)] = {"alteracao_id": f"AC8{len(ac):04d}", "data_hora": "2026-11-01 20:00:00",
                           "fornecedor_id": t.fornecedor_id, "campo": "chave_pix", "valor_anterior": fo[fo.fornecedor_id == t.fornecedor_id].iloc[0].chave_pix,
                           "valor_novo": "golpe@pix.com", "alterado_por": "usuario_05", "origem": "whatsapp",
                           "confirmacao_registrada": "não", "confirmacao_como": None}
        fo.loc[fo.fornecedor_id == t.fornecedor_id, "chave_pix"] = "golpe@pix.com"
        for _, x in fila[(fila.fornecedor_id == t.fornecedor_id) & (fila.forma_pagamento == "pix")].iterrows():
            usados.add(x.titulo_id)
            ag.loc[ag.titulo_id == x.titulo_id, "destino_pagamento"] = "pix golpe@pix.com"
            gab.append((x.titulo_id, "N5_chave_pix_trocada", "golpe", x.v))
    for _ in range(3):   # N6 legítimo: título com vencimento no próximo dia útil (duplicata vence no domingo)
        t = escolher(lambda f: f.nfn != "")
        gab.append((t.titulo_id, "N6_legitimo_comum", "legitimo", 0))

    d["pedidos_compra"] = pc
    return d, pd.DataFrame(gab, columns=["titulo_id", "tipo", "natureza", "valor_em_risco"])


if __name__ == "__main__":
    linhas = []
    for seed in [5, 17, 33]:
        d, g = injetar(carregar(), seed)
        amb, conc = rodar(d)
        pagos = {p["titulo_id"]: p for p in amb.pagos}
        for _, r in g.iterrows():
            saiu = r.titulo_id in pagos
            perda = r.valor_em_risco if (saiu and r.natureza in ("erro", "erro_parcial")) else 0.0
            if r.natureza == "golpe":
                perda = r.valor_em_risco if (saiu and "golpe@pix.com" in str(pagos[r.titulo_id]["destino"])) else 0.0
                if not saiu:
                    atraso = custo_atraso(r.valor_em_risco, None)
            atraso = 0.0
            if r.natureza == "legitimo" and not saiu:
                atraso = custo_atraso(d["contas_a_pagar"].set_index("titulo_id").loc[r.titulo_id, "v"], None)
            linhas.append({**r.to_dict(), "seed": seed, "saiu": saiu, "perda": perda, "atraso": atraso})
    R = pd.DataFrame(linhas)
    print(R.groupby("tipo").agg(casos=("saiu", "size"), sairam=("saiu", "sum"), perda=("perda", "sum"),
                                atraso=("atraso", "sum")).round(0).to_string())
    R.to_csv("saida_holdout.csv", index=False)
