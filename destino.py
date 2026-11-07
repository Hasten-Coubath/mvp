"""Destino confiável: o dinheiro vai para onde esse fornecedor de fato recebe?

TED/PIX  -> dados bancários do cadastro só valem se a última troca teve confirmação VÁLIDA:
            ligação para o telefone que estava no cadastro antes da alteração, e esse telefone
            não pode ter sido trocado nos 30 dias anteriores (política dados_bancarios.troca_de_conta).
Boleto   -> beneficiário com a raiz do CNPJ do fornecedor; banco cobrador já usado por ele
            (ou banco do cadastro); valor da linha digitável = valor do título.
"""
import re
import pandas as pd
from livro import so_digitos

CAMPOS_BANCARIOS = {"banco", "agencia", "conta", "chave_pix"}


def conta_cobranca(ld):
    """Banco + agência (4) + conta com dígito (8) embutidos no campo livre da linha digitável."""
    ld = str(ld)
    if len(ld) != 47:
        return ""
    livre = ld[4:9] + ld[10:20] + ld[21:31]
    return f"{ld[:3]}/{livre[:4]}/{livre[4:12]}"


def tel(s):
    return so_digitos(s)[-10:] if pd.notna(s) else ""


class Destino:
    def __init__(self, d):
        self.d = d
        fo = d["fornecedores"].set_index("fornecedor_id")
        self.fo = fo
        ac = d["alteracoes_cadastro"].copy()
        ac["ts"] = pd.to_datetime(ac.data_hora)
        self.ac = ac.sort_values("ts")
        # bancos cobradores confiáveis por fornecedor: boletos já liquidados na DDA com a raiz do fornecedor
        dda = d["dda_boletos"].copy()
        dda["raiz"] = dda.cnpj_beneficiario.map(so_digitos).str[:8]
        self.dda_linhas = set(dda.linha_digitavel)
        liq = dda[dda.situacao == "liquidado"]
        self.bancos_cobranca = liq.groupby("raiz").banco_cobrador.apply(set).to_dict()
        # conta de cobrança (banco + agência + conta do campo livre) já usada por cada fornecedor
        dda["conta_cob"] = dda.linha_digitavel.map(conta_cobranca)
        self.contas_cobranca = dda.groupby("raiz").conta_cob.apply(set).to_dict()

    # --- telefone que estava no cadastro num instante (reconstruído de trás para frente)
    def telefone_em(self, fid, instante):
        atual = tel(self.fo.loc[fid, "telefone_cadastrado"])
        mud = self.ac[(self.ac.fornecedor_id == fid) & (self.ac.campo == "telefone")]
        for _, m in mud.sort_values("ts", ascending=False).iterrows():
            if m.ts > instante:
                atual = tel(m.valor_anterior)
        return atual

    def ultima_troca_bancaria(self, fid, ate=None):
        t = self.ac[(self.ac.fornecedor_id == fid) & (self.ac.campo.isin(CAMPOS_BANCARIOS))]
        if ate is not None:
            t = t[t.ts <= pd.Timestamp(ate)]
        return None if t.empty else t.iloc[-1]

    def confirmacao_valida(self, troca):
        """Devolve (valida: bool, motivo: str)."""
        if troca.confirmacao_registrada != "sim" or pd.isna(troca.confirmacao_como):
            return False, "troca sem confirmação registrada"
        texto = str(troca.confirmacao_como)
        if "ligação" not in texto:
            return False, "confirmação não foi por ligação"
        m = re.search(r"\(\d{2}\)\s*[\d-]+", texto)
        if not m:
            return False, "confirmação sem telefone identificável"
        tel_conf = tel(m.group(0))
        fid, quando = troca.fornecedor_id, troca.ts
        tel_antes = self.telefone_em(fid, quando - pd.Timedelta(seconds=1))
        if tel_conf != tel_antes:
            return False, f"ligação para telefone que não era o do cadastro antes da troca"
        mud_tel = self.ac[(self.ac.fornecedor_id == fid) & (self.ac.campo == "telefone")
                          & (self.ac.ts < quando) & (self.ac.ts >= quando - pd.Timedelta(days=30))]
        if not mud_tel.empty:
            return False, "telefone do cadastro trocado menos de 30 dias antes da troca bancária"
        return True, "confirmada por ligação ao telefone anterior"

    def avaliar(self, t, data_pagto=None):
        """Devolve (status, motivo, evidência). status: OK | ATENCAO | DESTINO_SUSPEITO.
        data_pagto: avalia o cadastro como estava naquele dia (para o histórico)."""
        if t.forma_pagamento in ("ted", "pix"):
            troca = self.ultima_troca_bancaria(t.fornecedor_id, data_pagto)
            if troca is None:
                return "OK", "dados bancários originais do cadastro", None
            ok, motivo = self.confirmacao_valida(troca)
            ev = {"alteracao_id": troca.alteracao_id, "data": troca.data_hora, "origem": troca.origem,
                  "de": troca.valor_anterior, "para": troca.valor_novo}
            return ("OK" if ok else "DESTINO_SUSPEITO"), motivo, ev

        # boleto
        ld = str(t.linha_digitavel) if pd.notna(t.linha_digitavel) else ""
        if len(ld) != 47:
            return "DESTINO_SUSPEITO", "boleto sem linha digitável válida", None
        problemas = []
        raiz_benef = so_digitos(t.cnpj_beneficiario_boleto)[:8]
        if raiz_benef and raiz_benef != t.raiz:
            problemas.append(f"beneficiário com CNPJ de outra empresa ({t.cnpj_beneficiario_boleto})")
        banco = ld[:3]
        confiaveis = self.bancos_cobranca.get(t.raiz, set()) | {str(self.fo.loc[t.fornecedor_id, "banco"])}
        if banco not in confiaveis:
            problemas.append(f"banco cobrador {banco} nunca usado por este fornecedor ({sorted(confiaveis)})")
        valor_ld = int(ld[37:47]) / 100
        venc_ld = pd.Timestamp("2025-02-22") + pd.Timedelta(days=int(ld[33:37]) - 1000)
        pago_em = pd.Timestamp(data_pagto or t.vencimento)
        acresc = 0.0
        if pago_em > venc_ld:  # multa 2% + juros 1% a.m. pro rata são legítimos
            acresc = valor_ld * (0.02 + 0.01 * (pago_em - venc_ld).days / 30)
        if t.v > valor_ld + acresc + 0.10:
            problemas.append(f"título R$ {t.v:,.2f} acima do valor do boleto R$ {valor_ld:,.2f}")
        elif t.v < valor_ld - 0.10:
            problemas.append(f"ABAIXO: título R$ {t.v:,.2f} menor que o boleto R$ {valor_ld:,.2f} (pagaria a menos)")
        if ld not in self.dda_linhas:
            problemas.append("boleto não registrado na DDA contra o CNPJ do Gogroup")
            if conta_cobranca(ld) not in self.contas_cobranca.get(t.raiz, set()):
                problemas.append(f"conta de cobrança {conta_cobranca(ld)} nunca usada por este fornecedor "
                                 "e boleto fora da DDA (possível segunda via adulterada)")
        # 'fora da DDA' sozinho é sinal fraco (1,6% dos boletos pagos legítimos não estavam na DDA)
        fortes = [p for p in problemas if (not p.startswith("boleto não registrado")) and not p.startswith("ABAIXO")]
        if fortes:
            return "DESTINO_SUSPEITO", "; ".join(problemas), {"linha_digitavel": ld}
        if problemas:
            return "ATENCAO", "; ".join(problemas), {"linha_digitavel": ld}
        return "OK", "boleto do fornecedor, banco habitual, valor confere", None
