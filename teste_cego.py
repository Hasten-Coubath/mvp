"""Teste cego: injeta erros e casos legítimos de borda numa cópia da base e mede o placar do parametros.md.

Placar = -(dinheiro que saiu indevidamente + custo de atrasar legítimo + R$ 60 por acionamento desnecessário)

Honestidade:
- O injetor foi escrito pela mesma pessoa que escreveu os agentes. Para reduzir o viés, ele inclui tipos de
  erro que o sistema NÃO foi desenhado para pegar (marcados esperado='falha').
- Os 22 títulos da base que o sistema já tratou como exceção (CP3) saem da fila de teste, porque não temos
  gabarito para eles. Os demais títulos da base são assumidos legítimos.
"""
import copy
import random
import pandas as pd

from livro import carregar, so_digitos
from rodar import rodar

RELAC = {"produto": 3000, "frete": 1500, "embalagens": 800, "insumos_capa": 800, "aluguel": 500,
         "ti_saas": 400, "utilidades": 400, "servicos": 300, "marketing_agencia": 300, "influenciador": 250}


def custo_atraso(valor, categoria):
    return valor * (0.02 + 0.01 * 7 / 30) + RELAC.get(categoria, 300)


def novo_id(cp, n=[900000]):
    n[0] += 1
    return f"TP{n[0]}"


def injetar(d, seed=7):
    rnd = random.Random(seed)
    d = copy.deepcopy(d)
    cp, ag, fo = d["contas_a_pagar"], d["agenda_remessa"], d["fornecedores"]
    ac, nf, dda = d["alteracoes_cadastro"], d["notas_fiscais"], d["dda_boletos"]
    gab = []  # titulo_id, tipo, legitimo, indevido_se_sair, esperado

    # base limpa: tira da fila o que o CP3 já tratou como exceção (sem gabarito)
    amb0, conc0 = rodar(carregar())
    excecoes = {x["titulo_id"] for x in conc0.decisoes if x["acao"] != "segue"}
    ag = ag[~ag.titulo_id.isin(excecoes)].copy()
    fila = cp[cp.titulo_id.isin(ag.titulo_id)].copy()
    # só injeta em títulos "limpos": valor = duplicata (evita herdar erro da base, como o zero a menos de TP433092)
    from livro import Livro
    L0 = Livro(carregar())
    limpos = {t.titulo_id for _, t in fila.iterrows() if L0.avaliar(t)[0] == "OK"}
    fila = fila[fila.titulo_id.isin(limpos)]
    usados = set()

    def escolher(filtro=None):
        cand = fila if filtro is None else fila[filtro(fila)]
        cand = cand[~cand.titulo_id.isin(usados) & ~cand.fornecedor_id.isin({"FP4615"})]
        t = cand.sample(1, random_state=rnd.randint(0, 10**6)).iloc[0]
        usados.add(t.titulo_id)
        return t

    def clonar(t, **mud):
        nt = t.copy()
        nt["titulo_id"] = novo_id(cp)
        for k, v in mud.items():
            nt[k] = v
        nt["status"] = "agendado"
        return nt

    novos, novos_ag = [], []

    def agendar(nt, lote_de):
        a = ag[ag.titulo_id == lote_de].iloc[0].copy()
        a["titulo_id"], a["valor"], a["fornecedor_id"] = nt.titulo_id, str(nt.v), nt.fornecedor_id
        novos_ag.append(a)

    # ---------------- ERROS (dinheiro indevido se sair)
    for i in range(5):   # E1 duplicata via boleto_email, NF com formatação diferente, mesmo valor
        t = escolher(lambda f: f.nfn != "")
        nt = clonar(t, numero_nf_digitado="0000" + t.nfn + "-1", origem_lancamento="boleto_email",
                    data_lancamento="2026-11-01 10:00:00")
        novos.append(nt); agendar(nt, t.titulo_id)
        gab.append((nt.titulo_id, "E1_duplicata_mesmo_valor", False, nt.v, "pegar"))
    for i in range(3):   # E2 duplicata com valor diferente (boleto com juros)
        t = escolher(lambda f: f.nfn != "")
        nt = clonar(t, v=round(t.v * 1.023, 2), origem_lancamento="boleto_email", data_lancamento="2026-11-01 11:00:00")
        novos.append(nt); agendar(nt, t.titulo_id)
        gab.append((nt.titulo_id, "E2_duplicata_valor_diferente", False, nt.v, "pegar"))
    for i in range(3):   # E3 zero a mais (o título original é substituído)
        t = escolher(lambda f: (f.nfn != "") & (f.forma_pagamento != "boleto"))
        cp.loc[cp.titulo_id == t.titulo_id, "v"] = t.v * 10
        gab.append((t.titulo_id, "E3_zero_a_mais", True, t.v, "pegar"))
    for i in range(3):   # E4 sobrepreço pequeno (+7%) em título no prazo
        t = escolher(lambda f: (f.nfn != "") & (f.forma_pagamento != "boleto") & (f.vencimento >= "2026-11-03"))
        cp.loc[cp.titulo_id == t.titulo_id, "v"] = round(t.v * 1.07, 2)
        gab.append((t.titulo_id, "E4_sobrepreco_7pct", True, t.v, "pegar"))

    def trocar_conta(fid, confirmacao, como, quando="2026-10-31 15:00:00"):
        f = fo[fo.fornecedor_id == fid].iloc[0]
        for campo, novo in [("banco", "905"), ("agencia", "0001"), ("conta", "1234567-8")]:
            ac.loc[len(ac)] = {"alteracao_id": f"AC9{len(ac):04d}", "data_hora": quando, "fornecedor_id": fid,
                               "campo": campo, "valor_anterior": f[campo], "valor_novo": novo, "alterado_por": "usuario_03",
                               "origem": "email", "confirmacao_registrada": confirmacao, "confirmacao_como": como}
            fo.loc[fo.fornecedor_id == fid, campo] = novo

    ted = lambda f: f.forma_pagamento.isin(["ted", "pix"]) & (f.nfn != "")
    for i in range(2):   # E5 conta trocada sem confirmação (todos os títulos TED/PIX do fornecedor viram golpe)
        t = escolher(ted)
        trocar_conta(t.fornecedor_id, "não", None)
        for _, x in fila[(fila.fornecedor_id == t.fornecedor_id) & fila.forma_pagamento.isin(["ted", "pix"])].iterrows():
            usados.add(x.titulo_id)
            gab.append((x.titulo_id, "E5_conta_trocada_sem_confirmacao", True, x.v, "pegar"))
    # E6 telefone trocado há 10 dias e a troca de conta "confirmada" ligando para o telefone NOVO
    t = escolher(ted)
    f = fo[fo.fornecedor_id == t.fornecedor_id].iloc[0]
    ac.loc[len(ac)] = {"alteracao_id": f"AC9{len(ac):04d}", "data_hora": "2026-10-21 09:00:00",
                       "fornecedor_id": t.fornecedor_id, "campo": "telefone", "valor_anterior": f.telefone_cadastrado,
                       "valor_novo": "(85) 99999-0000", "alterado_por": "usuario_03", "origem": "email",
                       "confirmacao_registrada": "sim", "confirmacao_como": "confirmado por e-mail"}
    fo.loc[fo.fornecedor_id == t.fornecedor_id, "telefone_cadastrado"] = "(85) 99999-0000"
    trocar_conta(t.fornecedor_id, "sim", "ligação para o telefone do cadastro (85) 99999-0000 em 31/10/2026; confirmado")
    for _, x in fila[(fila.fornecedor_id == t.fornecedor_id) & fila.forma_pagamento.isin(["ted", "pix"])].iterrows():
        usados.add(x.titulo_id)
        gab.append((x.titulo_id, "E6_confirmacao_no_telefone_novo", True, x.v, "pegar"))

    bol = lambda f: (f.forma_pagamento == "boleto") & (f.nfn != "")
    for i in range(2):   # E7 boleto adulterado com CNPJ de outra empresa
        t = escolher(bol)
        cp.loc[cp.titulo_id == t.titulo_id, "cnpj_beneficiario_boleto"] = "11.222.333/0001-81"
        ld = "905" + t.linha_digitavel[3:]
        cp.loc[cp.titulo_id == t.titulo_id, "linha_digitavel"] = ld
        gab.append((t.titulo_id, "E7_boleto_outro_cnpj", True, t.v, "pegar"))
    for i in range(2):   # E8 boleto adulterado: mesmo CNPJ e banco, só a conta no campo livre muda
        t = escolher(bol)
        ld = t.linha_digitavel
        ld = ld[:11] + "99999999" + ld[19:]
        cp.loc[cp.titulo_id == t.titulo_id, "linha_digitavel"] = ld
        gab.append((t.titulo_id, "E8_boleto_conta_adulterada_mesmo_cnpj", True, t.v, "pegar (após CP4 v1)"))
    for i in range(2):   # E9 já pago por fora do lote (PIX manual), sem baixa no ERP
        t = escolher(ted)
        nome = fo[fo.fornecedor_id == t.fornecedor_id].iloc[0].razao_social.upper()
        nome = nome.translate(str.maketrans("ÁÀÃÂÉÊÍÓÔÕÚÇ", "AAAAEEIOOOUC"))[:22]
        sb = d["saidas_banco"]
        sb.loc[len(sb)] = {"data": "2026-10-30", "descricao": f"PIX ENV 81999 {nome}", "v": round(t.v, 2),
                           "banco": "LIT", "documento": None}
        gab.append((t.titulo_id, "E9_ja_pago_fora_do_lote", False, t.v, "pegar (após CP4 v1)"))
    for i in range(2):   # E10 título fantasma: NF que não existe
        t = escolher(lambda f: f.nfn != "")
        nt = clonar(t, numero_nf_digitado="88" + t.nfn, nfn="88" + t.nfn, data_lancamento="2026-11-01 12:00:00")
        novos.append(nt); agendar(nt, t.titulo_id)
        gab.append((nt.titulo_id, "E10_nf_inexistente", False, nt.v, "pegar"))
    # E11 fracionamento: um título de R$ 9 mil partido em dois de R$ 4,5 mil (mesma NF) para caber na alçada
    t = escolher(lambda f: (f.nfn != "") & (f.v > 6000) & (f.v < 10000))
    nt = clonar(t, v=round(t.v / 2, 2), data_lancamento="2026-11-01 13:00:00")
    cp.loc[cp.titulo_id == t.titulo_id, "v"] = round(t.v / 2, 2)
    novos.append(nt); agendar(nt, t.titulo_id)
    gab.append((nt.titulo_id, "E11_fracionamento_mesma_NF", True, nt.v, "falha"))

    # ---------------- LEGÍTIMOS DE BORDA (atraso custa)
    for i in range(3):   # L1 NF emitida por filial (mesma raiz, outro final)
        t = escolher(lambda f: f.nfn != "")
        m = (nf.cnpj_emitente.str[:8] == t.raiz) & (nf.numero_nf.str.lstrip("0") == t.nfn)
        nf.loc[m, "cnpj_emitente"] = nf.loc[m, "cnpj_emitente"].str[:8] + "000299"
        gab.append((t.titulo_id, "L1_nf_de_filial", True, 0, "pagar"))
    for i in range(3):   # L2 NF digitada com zeros e série
        t = escolher(lambda f: f.nfn != "")
        cp.loc[cp.titulo_id == t.titulo_id, "nfn"] = t.nfn
        cp.loc[cp.titulo_id == t.titulo_id, "numero_nf_digitado"] = "000" + t.nfn + "/2"
        gab.append((t.titulo_id, "L2_nf_com_zeros_e_serie", True, 0, "pagar"))
    for i in range(3):   # L3 diferença de arredondamento de R$ 0,07
        t = escolher(lambda f: (f.nfn != "") & (f.forma_pagamento != "boleto"))
        cp.loc[cp.titulo_id == t.titulo_id, "v"] = round(t.v + 0.07, 2)
        gab.append((t.titulo_id, "L3_arredondamento", True, 0, "pagar"))
    t = escolher(ted)    # L4 troca de conta legítima, confirmada no telefone ANTERIOR
    tel_ant = fo[fo.fornecedor_id == t.fornecedor_id].iloc[0].telefone_cadastrado
    trocar_conta(t.fornecedor_id, "sim", f"ligação para o telefone do cadastro {tel_ant} em 31/10/2026; confirmado")
    for _, x in fila[(fila.fornecedor_id == t.fornecedor_id) & fila.forma_pagamento.isin(["ted", "pix"])].iterrows():
        usados.add(x.titulo_id)
        gab.append((x.titulo_id, "L4_troca_de_conta_confirmada", True, 0, "pagar"))
    for i in range(2):   # L5 boleto legítimo que não aparece na DDA
        t = escolher(bol)
        dda.drop(dda[dda.linha_digitavel == t.linha_digitavel].index, inplace=True)
        gab.append((t.titulo_id, "L5_boleto_fora_da_DDA", True, 0, "pagar"))

    if novos:
        cp = pd.concat([cp, pd.DataFrame(novos)], ignore_index=True)
    ag = pd.concat([ag, pd.DataFrame(novos_ag)], ignore_index=True)
    cp["valor"] = cp.v.map(lambda x: f"{x:.2f}")
    from livro import nf_normal
    cp["nfn"] = cp.numero_nf_digitado.map(nf_normal)
    d.update({"contas_a_pagar": cp, "agenda_remessa": ag, "fornecedores": fo,
              "alteracoes_cadastro": ac.reset_index(drop=True), "notas_fiscais": nf, "dda_boletos": dda})
    g = pd.DataFrame(gab, columns=["titulo_id", "tipo", "obrigacao_legitima", "indevido", "esperado"])
    return d, g


def placar(d, g, amb):
    """indevido: E3/E4 = valor correto (perda = o que saiu acima dele); demais E = valor do título."""
    pagos = pd.DataFrame(amb.pagos).set_index("titulo_id")
    tit = d["contas_a_pagar"].set_index("titulo_id")
    linhas = []
    for _, r in g.iterrows():
        saiu = r.titulo_id in pagos.index
        valor_pago = float(pagos.loc[r.titulo_id, "valor"]) if saiu else 0.0
        destino = str(pagos.loc[r.titulo_id, "destino"]) if saiu else ""
        perda, atraso = 0.0, 0.0
        if r.tipo.startswith(("E3", "E4")):
            perda = max(0.0, valor_pago - r.indevido - 0.10)
            if not saiu:
                atraso = custo_atraso(r.indevido, tit.loc[r.titulo_id, "categoria"])
        elif r.tipo.startswith(("E5", "E6")):
            if saiu and "905/0001/1234567-8" in destino:
                perda = r.indevido
            if not saiu:
                atraso = custo_atraso(r.indevido, tit.loc[r.titulo_id, "categoria"])
        elif r.tipo.startswith(("E7", "E8")):
            perda = valor_pago            # boleto adulterado: o que sai vai para o golpista
        elif r.tipo.startswith("E11"):     # certo = a parcela inteira sair uma vez (somando as metades)
            c = tit.loc[r.titulo_id]
            irm = tit[(tit.fornecedor_id == c.fornecedor_id) & (tit.nfn == c.nfn) & (tit.pn == c.pn)].index
            total_pago = sum(float(pagos.loc[x, "valor"]) for x in irm if x in pagos.index)
            devido = 2 * r.indevido
            if total_pago < devido - 0.25:
                atraso = custo_atraso(devido - total_pago, c.categoria)
            perda = max(0.0, total_pago - devido - 0.25)
        elif r.tipo.startswith("E"):
            perda = valor_pago
        else:                              # legítimos de borda
            if not saiu:
                atraso = custo_atraso(tit.loc[r.titulo_id, "v"], tit.loc[r.titulo_id, "categoria"])
        linhas.append({**r.to_dict(), "saiu": saiu, "valor_pago": valor_pago,
                       "perda": round(perda, 2), "atraso": round(atraso, 2)})
    res = pd.DataFrame(linhas)
    legit_ids = set(g[g.tipo.str.startswith("L")].titulo_id)
    acion = pd.DataFrame(amb.acionamentos)
    desnec = 0
    if len(acion):
        desnec = int(acion.titulo_id.fillna("").map(lambda s: any(t in legit_ids for t in s.split(","))).sum())
    return res, desnec


def perda_sem_agente(g, d):
    tit = d["contas_a_pagar"].set_index("titulo_id")
    tot = 0.0
    for _, r in g.iterrows():
        if r.tipo.startswith(("E3", "E4")):
            tot += tit.loc[r.titulo_id, "v"] - r.indevido
        elif r.tipo.startswith("E") and not r.tipo.startswith("E11"):
            tot += r.indevido
    return tot


def _corrigido(amb, tid):
    return any(l["ferramenta"] == "corrigir_lancamento" and l["titulo_id"] == tid for l in amb.log)


if __name__ == "__main__":
    total = []
    for seed in [7, 21, 42, 99, 123]:
        d, g = injetar(carregar(), seed)
        amb, conc = rodar(d)
        res, desnec = placar(d, g, amb)
        for _, r in res[(res.perda > 0) | (res.atraso > 0)].iterrows():
            dec = [x for x in conc.decisoes if x["titulo_id"] == r.titulo_id]
            print("   ERRO:", r.tipo, r.titulo_id, f"perda {r.perda:,.0f} atraso {r.atraso:,.0f}", dec[-1] if dec else "sem decisão")
        base_perda = perda_sem_agente(g, d)
        res["seed"] = seed
        total.append(res)
        p, a = res.perda.sum(), res.atraso.sum()
        print(f"seed {seed}: sem agente perde R$ {base_perda:,.0f} | com agente: perda R$ {p:,.0f}, "
              f"atraso R$ {a:,.0f}, acionamentos desnecessários {desnec} (R$ {60*desnec})")
    R = pd.concat(total)
    R["acertou"] = (R.perda == 0) & (R.atraso == 0)
    print("\nPor tipo (5 sementes):")
    print(R.groupby(["tipo", "esperado"]).agg(casos=("acertou", "size"), acertos=("acertou", "sum"),
                                              perda=("perda", "sum"), atraso=("atraso", "sum")).round(0).to_string())
    R.to_csv("saida_teste_cego.csv", index=False)
