"""Livro de Obrigações: nenhum real sai sem uma dívida aberta que o justifique.

Obrigação = duplicata de NF no portal (chave: raiz CNPJ + número NF + parcela)
           ou parcela mensal de contrato sem NF (aluguel: fornecedor + competência).
Cada obrigação só pode ser consumida uma vez.
"""
import re
from pathlib import Path
import pandas as pd

DADOS = Path(__file__).parent / "dados"


def so_digitos(s):
    return re.sub(r"\D", "", str(s)) if pd.notna(s) else ""


def nf_normal(s):
    """'000011045' -> '11045'; '11079-1' -> '11079'; '10252/3' -> '10252'."""
    if pd.isna(s) or str(s).strip() == "":
        return ""
    return re.split(r"[-/ ]", str(s).strip())[0].lstrip("0")


def carregar():
    rd = lambda f: pd.read_csv(DADOS / f, dtype=str)
    d = {k: rd(f"{k}.csv") for k in [
        "contas_a_pagar", "agenda_remessa", "fornecedores", "notas_fiscais",
        "nf_duplicatas", "contratos_fornecedores", "alteracoes_cadastro",
        "dda_boletos", "comunicacoes_fornecedores", "politicas", "usuarios_sistema"]}
    fo = d["fornecedores"]
    fo["raiz"] = fo.cnpj.map(so_digitos).str[:8]
    cp = d["contas_a_pagar"].merge(fo[["fornecedor_id", "raiz", "categoria"]], on="fornecedor_id", how="left")
    cp["v"] = cp.valor.astype(float)
    cp["nfn"] = cp.numero_nf_digitado.map(nf_normal)
    cp["pn"] = cp.parcela.str.split("/").str[0].astype(int)
    cp["competencia"] = cp.historico.str.extract(r"competência (\d{2}/\d{4})")[0]
    d["contas_a_pagar"] = cp
    d["saidas_banco"] = carregar_extratos()
    return d


def carregar_extratos():
    """Saídas de dinheiro dos dois bancos num formato só (Litoral vem com ; e vírgula decimal)."""
    em = pd.read_csv(DADOS / "extrato_banco_meridiano.csv", dtype=str)
    em = em[em.tipo.str.startswith("PAGTO")]
    em = pd.DataFrame({"data": em.data, "descricao": em.descricao, "v": (-em.valor.astype(float)).round(2),
                       "banco": "MER", "documento": em.documento})
    el = pd.read_csv(DADOS / "extrato_banco_litoral.csv", sep=";", dtype=str)
    el = el[el["Histórico"].str.match(r"(PIX ENV|TED ENV|PAG BOL)")]
    val = el["Valor (R$)"].str.replace(".", "", regex=False).str.replace(",", ".").astype(float)
    el = pd.DataFrame({"data": pd.to_datetime(el.Data, dayfirst=True).dt.strftime("%Y-%m-%d"),
                       "descricao": el["Histórico"], "v": (-val).round(2), "banco": "LIT", "documento": None})
    return pd.concat([em, el], ignore_index=True)


def montar_obrigacoes(d):
    nf, du = d["notas_fiscais"], d["nf_duplicatas"]
    nf = nf.assign(raiz=nf.cnpj_emitente.str[:8], nfn=nf.numero_nf.str.lstrip("0"))
    o = du.merge(nf[["chave_acesso", "raiz", "nfn", "modelo", "cnpj_emitente"]], on="chave_acesso")
    o = o.assign(pn=o.n_dup.astype(int), valor_devido=o.valor.astype(float), origem="nf")
    o["chave"] = "NF|" + o.raiz + "|" + o.nfn + "|" + o.pn.astype(str)
    o = o[["chave", "origem", "raiz", "nfn", "pn", "valor_devido", "vencimento", "chave_acesso"]]

    # Contratos sem NF (aluguel): uma obrigação por competência mensal na vigência
    ct = d["contratos_fornecedores"]
    fo = d["fornecedores"]
    sem_nf = ct.merge(fo[["fornecedor_id", "categoria", "raiz"]], on="fornecedor_id")
    sem_nf = sem_nf[sem_nf.categoria == "aluguel"]
    linhas = []
    for _, c in sem_nf.iterrows():
        for mes in pd.period_range("2026-01", "2026-12", freq="M"):
            comp = f"{mes.month:02d}/{mes.year}"
            linhas.append({"chave": f"CT|{c.fornecedor_id}|{comp}", "origem": "contrato",
                           "raiz": c.raiz, "nfn": "", "pn": 1, "valor_devido": float(c.valor_mensal),
                           "vencimento": f"{mes.year}-{mes.month:02d}-{int(c.dia_vencimento):02d}",
                           "chave_acesso": c.contrato_id})
    o = pd.concat([o, pd.DataFrame(linhas)], ignore_index=True)
    return o.set_index("chave")


def chave_do_titulo(t):
    if t.nfn:
        return f"NF|{t.raiz}|{t.nfn}|{t.pn}"
    if t.categoria == "aluguel" and pd.notna(t.competencia):
        return f"CT|{t.fornecedor_id}|{t.competencia}"
    return None


def valor_aceito(valor_titulo, valor_devido, venc_dup, data_pagto):
    """Tolerância da política: R$ 0,10 + (se pago após vencimento) multa 2% + juros 1% a.m. pro rata."""
    teto = valor_devido + 0.10
    if data_pagto and venc_dup and data_pagto > venc_dup:
        dias = (pd.Timestamp(data_pagto) - pd.Timestamp(venc_dup)).days
        teto += valor_devido * (0.02 + 0.01 * dias / 30)
    return valor_titulo <= teto


class Livro:
    def __init__(self, d):
        self.d = d
        self.obr = montar_obrigacoes(d)
        self.consumida_por = {}   # chave -> titulo_id que consumiu

    def avaliar(self, t, data_pagto=None):
        """Responde às perguntas 1 e 2 para um título. Não consome."""
        k = chave_do_titulo(t)
        if k is None:
            return "SEM_OBRIGACAO", k, None
        if k not in self.obr.index:
            return "SEM_OBRIGACAO", k, None
        if k in self.consumida_por:
            return "JA_CONSUMIDA", k, self.consumida_por[k]
        o = self.obr.loc[k]
        if not valor_aceito(t.v, o.valor_devido, o.vencimento, data_pagto or t.vencimento):
            return "VALOR_ACIMA", k, o.valor_devido
        if t.v < o.valor_devido - 0.10:
            return "VALOR_ABAIXO", k, o.valor_devido   # paga a menos: não é perda, mas gera cobrança/atraso
        return "OK", k, o.valor_devido

    def consumir(self, k, titulo_id):
        self.consumida_por[k] = titulo_id

    def conciliar_extrato(self):
        """Toda saída do banco precisa de um título pago. Saída sem título = pagamento manual sem baixa:
        procura o título em aberto do mesmo fornecedor e valor e consome a obrigação dele."""
        from collections import Counter
        cp = self.d["contas_a_pagar"]
        pg = cp[cp.status == "pago"]
        bk = pg.banco_pagador.map({"Banco Meridiano": "MER", "Banco Litoral": "LIT"})
        cnt = Counter(pg.data_pagamento + "|" + pg.v.round(2).astype(str) + "|" + bk)
        fo = self.d["fornecedores"].set_index("fornecedor_id")
        abertos = cp[cp.status.isin(["agendado", "aberto"])]
        achados = []
        for _, s in self.d["saidas_banco"].iterrows():
            k = f"{s.data}|{s.v}|{s.banco}"
            if cnt[k] > 0:
                cnt[k] -= 1
                continue
            cand = abertos[(abertos.v - s.v).abs() < 0.01]
            for _, t in cand.iterrows():
                nomes = (str(fo.loc[t.fornecedor_id, "razao_social"]) + " " +
                         str(fo.loc[t.fornecedor_id, "nome_fantasia"])).upper()
                nomes = nomes.translate(str.maketrans("ÁÀÃÂÉÊÍÓÔÕÚÇ", "AAAAEEIOOOUC"))
                palavras = [w for w in re.findall(r"[A-Z]{4,}", nomes) if w not in {"LTDA", "INDUSTRIA", "COMERCIO"}]
                if any(w[:8] in s.descricao.upper() for w in palavras):
                    chave = chave_do_titulo(t)
                    if chave and chave not in self.consumida_por:
                        self.consumir(chave, f"extrato {s.banco} {s.data} '{s.descricao}'")
                        achados.append((t.titulo_id, s.banco, s.data, s.descricao, s.v))
                    break
        return achados

    def replay_historico(self):
        """Consome obrigações com tudo o que já foi pago (lote ou avulso), em ordem de pagamento."""
        cp = self.d["contas_a_pagar"]
        pagos = cp[cp.status == "pago"].sort_values(["data_pagamento", "data_lancamento"])
        res = []
        for _, t in pagos.iterrows():
            st, k, info = self.avaliar(t, t.data_pagamento)
            if k and k not in self.consumida_por and k in self.obr.index:
                self.consumir(k, t.titulo_id)
            res.append((t.titulo_id, st, k, info, t.v))
        return pd.DataFrame(res, columns=["titulo_id", "status", "chave", "info", "v"])
