"""Lançamento assistido: resposta ao desafio "cadastro manual repetido em vários sistemas".

Hoje a equipe digita cada título olhando a NF ou o boleto. O agente passa a montar o rascunho do título a
partir do XML (fonte única) e o analista só confirma. O boleto que chega por e-mail não vira título novo:
é vinculado ao título da mesma NF/parcela.

Limite assumido: o catálogo de ferramentas do desafio não tem 'lançar título'. Por isso o agente prepara
o rascunho e um humano confirma (o agente faz só o que está ao alcance dele).

Medição honesta: comparo o rascunho que o agente faria com o que a equipe digitou de fato no histórico.
"""
import pandas as pd
from livro import carregar, so_digitos


def rascunhos(d):
    nf, du, fo = d["notas_fiscais"], d["nf_duplicatas"], d["fornecedores"]
    raiz2forn = fo.assign(raiz=fo.cnpj.map(so_digitos).str[:8]).groupby("raiz").fornecedor_id.first()
    x = du.merge(nf, on="chave_acesso")
    x = x.assign(raiz=x.cnpj_emitente.str[:8], nfn=x.numero_nf.str.lstrip("0"), pn=x.n_dup.astype(int),
                 tot=x.groupby("chave_acesso").n_dup.transform("count"))
    x["fornecedor_id"] = x.raiz.map(raiz2forn)
    return pd.DataFrame({"fornecedor_id": x.fornecedor_id, "raiz": x.raiz, "nf": x.nfn,
                         "parcela": x.pn.astype(str) + "/" + x.tot.astype(str), "pn": x.pn,
                         "valor": x.valor.astype(float), "vencimento": x.vencimento, "oc_id": x.oc_informada,
                         "chave_acesso": x.chave_acesso, "situacao_sefaz": x.situacao_sefaz})


def medir():
    d = carregar()
    R = rascunhos(d)
    cp = d["contas_a_pagar"]
    cp = cp[cp.status != "cancelado"]
    com_nf = cp[cp.nfn != ""]
    m = com_nf.merge(R, left_on=["raiz", "nfn", "pn"], right_on=["raiz", "nf", "pn"], how="left", suffixes=("", "_xml"))
    achou = m.valor_xml.notna()
    res = {
        "títulos digitados com NF": len(com_nf),
        "rascunho do XML encontrado (cobertura)": f"{achou.mean():.1%}",
        "fornecedor identificado pelo CNPJ do XML": f"{m[achou].fornecedor_id_xml.notna().mean():.1%}",
        "número da NF digitado em formato diferente do XML": int((m[achou].numero_nf_digitado.str.strip() != m[achou].nf).sum()),
        "valor digitado diferente da duplicata (> R$ 0,10)": int(((m[achou].v - m[achou].valor_xml).abs() > 0.10).sum()),
        "vencimento digitado diferente da duplicata": int((m[achou].vencimento != m[achou].vencimento_xml).sum()),
        "títulos que eram cópia de outro (boleto por e-mail lançado de novo)": int(m[achou].duplicated(["raiz", "nfn", "pn"]).sum()),
    }
    # backlog: NFs autorizadas que chegaram e ainda não têm título -> rascunho pronto para o analista
    chaves_lancadas = set(m[achou].raiz + "|" + m[achou].nfn + "|" + m[achou].pn.astype(str))
    R["k"] = R.raiz + "|" + R.nf + "|" + R.pn.astype(str)
    backlog = R[~R.k.isin(chaves_lancadas) & (R.situacao_sefaz == "autorizada") & (R.vencimento >= "2026-11-02")]
    res["parcelas de NF já recebidas sem título (rascunhos prontos)"] = len(backlog)
    res["valor desses rascunhos"] = f"R$ {backlog.valor.sum():,.2f}"
    # vencimento: diferença é erro ou ajuste legítimo para dia útil?
    dif = m[achou & (m.vencimento != m.vencimento_xml)]
    dias = (pd.to_datetime(dif.vencimento) - pd.to_datetime(dif.vencimento_xml)).dt.days
    res["  dessas diferenças de vencimento, até +3 dias (ajuste p/ dia útil)"] = int(dias.between(0, 3).sum())
    return res, backlog


if __name__ == "__main__":
    res, backlog = medir()
    for k, v in res.items():
        print(f"{k}: {v}")
    backlog.drop(columns=["k"]).to_csv("saida_rascunhos_lancamento.csv", index=False)
