"""Conferência de 3 vias, escrita a partir do texto da política (politicas.csv, seção tres_vias e alcadas).
Nenhum limite aqui foi ajustado olhando resultado de teste.

- NF: a duplicata só é dívida se a NF estiver autorizada na SEFAZ.
- OC: categorias dispensadas de OC = utilidades; de NF = aluguel.
      OC cancelada  -> não há compra válida (retém e aciona comprador).
      sem OC / OC pendente -> 'pagamento sem OC aprovada' exige aprovação dupla.
- NF x OC: NF pode passar da OC em até 2% ou R$ 50 (o que for maior); acima disso, acionar o comprador.
  OC de contrato: referência = valor mensal. OC pontual com várias NFs: soma das NFs da OC.
"""
import pandas as pd

DISPENSA_OC = {"utilidades", "aluguel"}


class TresVias:
    def __init__(self, d):
        pc = d.get("pedidos_compra")
        if pc is None:
            from livro import DADOS
            pc = pd.read_csv(DADOS / "pedidos_compra.csv", dtype=str)
        self.oc = pc.set_index("oc_id")
        nf = d["notas_fiscais"]
        self.nf = nf.assign(raiz=nf.cnpj_emitente.str[:8], nfn=nf.numero_nf.str.lstrip("0"),
                            vt=nf.valor_total.astype(float))

    def _nf(self, t):
        m = self.nf[(self.nf.raiz == t.raiz) & (self.nf.nfn == t.nfn)]
        return None if m.empty else m.iloc[0]

    def avaliar(self, t, historico=False):
        """Devolve (status, motivo). status: OK | NF_INVALIDA | OC_CANCELADA | SEM_OC_APROVADA | NF_ACIMA_DA_OC"""
        if t.nfn:
            n = self._nf(t)
            if n is not None and str(n.situacao_sefaz) != "autorizada":
                return "NF_INVALIDA", f"NF {t.nfn} está '{n.situacao_sefaz}' na SEFAZ"
        if t.categoria in DISPENSA_OC:
            return "OK", "categoria dispensada de OC"
        oc_id = t.oc_id if pd.notna(t.oc_id) and t.oc_id in self.oc.index else None
        if oc_id is None:
            return "SEM_OC_APROVADA", "título sem OC válida no sistema de Compras"
        oc = self.oc.loc[oc_id]
        validos = {"aprovada", "encerrada"} if historico else {"aprovada"}
        if oc.status == "cancelada":
            return "OC_CANCELADA", f"{oc_id} cancelada em Compras"
        if oc.status not in validos:
            return "SEM_OC_APROVADA", f"{oc_id} está '{oc.status}'"
        if t.nfn:
            if oc.tipo == "contrato":
                ref = float(oc.valor_mensal)
                n = self._nf(t)
                faturado = n.vt if n is not None else t.v
            else:
                ref = float(oc.valor_total)
                n = self._nf(t)
                mesma_oc = self.nf[self.nf.oc_informada == oc_id]
                faturado = mesma_oc.vt.sum()
                if n is None:
                    faturado += 0 if len(mesma_oc) else t.v
                elif n.chave_acesso not in set(mesma_oc.chave_acesso):
                    faturado += n.vt
            tol = max(0.02 * ref, 50.0)
            if faturado > ref + tol:
                return "NF_ACIMA_DA_OC", (f"faturado R$ {faturado:,.2f} contra {oc_id} de R$ {ref:,.2f} "
                                          f"(tolerância R$ {tol:,.2f})")
        return "OK", "OC aprovada e NF dentro da tolerância"
