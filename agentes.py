"""Dois colegas:
- Conciliador (papel auditor): dono do Livro. Antes de cada fechamento, decide o que segue sozinho.
  Tira do lote o que não tem motivo para sair e trata fraude de destino (reverter cadastro).
- Analista (papel analista_cp): resolve as exceções para que o legítimo saia no prazo:
  cancela duplicidade, corrige valor pela NF, reagenda, fala com fornecedor pelo canal do cadastro
  e levanta a mão para a pessoa certa.
"""
from collections import defaultdict
import pandas as pd

from livro import Livro
from destino import Destino
from tres_vias import TresVias
from ambiente import AcaoNegada

def brl(v):
    return "R$ " + f"{float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


COMPRADOR_POR_CATEGORIA = {"produto": "PS04", "embalagens": "PS04", "insumos_capa": "PS05", "servicos": "PS05",
                           "marketing_agencia": "PS06", "influenciador": "PS06", "frete": "PS07",
                           "aluguel": "PS10", "utilidades": "PS10", "ti_saas": "PS11"}


class Conciliador:
    nome, papel = "agente_conciliador", "auditor"

    def __init__(self, amb, analista, em_verificacao=None):
        self.amb, self.analista = amb, analista
        self.em_verificacao = em_verificacao or {}   # fornecedor -> e-mail que abriu a verificação (IA)
        self.livro = Livro(amb.d)
        self.livro.replay_historico()
        self.pagos_fora = self.livro.conciliar_extrato()
        self.destino = Destino(amb.d)
        self.tres_vias = TresVias(amb.d)
        self.revertidos = set()
        self.decisoes = []
        self.decididos = set()

    def varrer_abertos(self):
        """Antes dos lotes: títulos em aberto (ainda sem lote) que copiam a NF/parcela de outro título.
        Cancela já, num pedido de aprovação em lote só, em vez de esperar o vencimento."""
        amb = self.amb
        cp = amb.titulos.reset_index()
        cp = cp[(cp.status != "cancelado") & (cp.nfn != "")].sort_values("data_lancamento")
        dup = cp[cp.duplicated(["raiz", "nfn", "pn"], keep="first") & (cp.status == "aberto")]
        if dup.empty:
            return
        orig = cp.drop_duplicates(["raiz", "nfn", "pn"], keep="first").set_index(["raiz", "nfn", "pn"]).titulo_id
        evid = [f"{r.titulo_id} copia {orig[(r.raiz, r.nfn, r.pn)]} (NF {r.nfn} parc {r.pn}, origem {r.origem_lancamento})"
                for _, r in dup.iterrows()]
        amb.solicitar_aprovacao(self.analista.nome, self.analista.papel, "cancelar_titulo",
                                {"titulos": list(dup.titulo_id)}, evid, float(dup.v.sum()),
                                motivo=f"{len(dup)} títulos em aberto duplicam NF/parcela já lançada (pedido único)")
        for _, r in dup.iterrows():
            def efeito(r=r):
                amb.cancelados.add(r.titulo_id)
                amb._log(self.analista.nome, "cancelar_titulo", "ok", "duplicidade em aberto (varredura preventiva)",
                         r.titulo_id, [f"copia {orig[(r.raiz, r.nfn, r.pn)]}"])
            amb.quando_aprovado(amb.deps(r.titulo_id), efeito)
            self.decisoes.append({"lote": "varredura", "titulo_id": r.titulo_id, "fornecedor_id": r.fornecedor_id,
                                  "valor": r.v, "livro": "DUPLICADO_EM_ABERTO", "chave": None, "destino": None,
                                  "motivo_destino": "", "tres_vias": None, "motivo_tres_vias": "",
                                  "acao": "cancelado_duplicidade_em_aberto"})

    def revisar_lote(self, lote):
        amb = self.amb
        # um título já decidido (e que voltou ao lote depois de uma aprovação) não é decidido de novo:
        # sem isso, a reserva da própria obrigação parecia duplicidade e o título legítimo era cancelado
        tids = [t for t, l in amb.lote_de.items() if l == lote and t not in self.decididos]
        tids.sort(key=lambda t: amb.titulos.loc[t, "data_lancamento"])
        for tid in tids:
            t = amb.titulos.loc[tid].copy()
            t["titulo_id"] = tid
            st_l, chave, info = self.livro.avaliar(t)
            st_d, motivo_d, ev_d = self.destino.avaliar(t)
            if st_d == "ATENCAO" and t.fornecedor_id in self.em_verificacao and t.forma_pagamento == "boleto":
                st_d = "DESTINO_SUSPEITO"     # IA abriu verificação: sinal fraco vira bloqueio (só aperta)
                motivo_d += f"; fornecedor em verificação pelo e-mail {self.em_verificacao[t.fornecedor_id]}"
            if st_d == "DESTINO_SUSPEITO" and t.fornecedor_id in self.revertidos and t.forma_pagamento in ("ted", "pix"):
                st_d, motivo_d = "OK", "cadastro revertido para a conta anterior confirmada"
            st_v, motivo_v = self.tres_vias.avaliar(t)
            dec = {"lote": lote, "titulo_id": tid, "fornecedor_id": t.fornecedor_id, "valor": t.v,
                   "livro": st_l, "chave": chave, "destino": st_d, "motivo_destino": motivo_d,
                   "tres_vias": st_v, "motivo_tres_vias": motivo_v}
            if st_l in ("OK", "VALOR_ACIMA", "VALOR_ABAIXO") and st_v in ("NF_INVALIDA", "OC_CANCELADA", "NF_ACIMA_DA_OC"):
                amb.retirar_do_lote(self.nome, self.papel, tid, f"3 vias: {motivo_v}")
                dec["acao"] = self.analista.resolver_tres_vias(t, st_v, motivo_v)
                self.decisoes.append(dec)
                self.decididos.add(tid)
                continue
            if st_l == "OK" and st_v == "SEM_OC_APROVADA" and st_d in ("OK", "ATENCAO"):
                amb.solicitar_aprovacao(self.nome, self.papel, "agendar_titulo", {"titulo_id": tid, "lote": lote},
                                        [motivo_v, f"obrigação {chave} aberta", motivo_d], t.v, dupla=True,
                                        motivo="pagamento sem OC aprovada (política: aprovação dupla)")

            if st_l == "OK" and st_d in ("OK", "ATENCAO") and not motivo_d.startswith("ABAIXO"):
                self.livro.consumir(chave, tid)           # reserva: este título é o pagamento desta dívida
                dec["acao"] = "segue"
            elif (st_l == "VALOR_ABAIXO" or (st_l == "OK" and motivo_d.startswith("ABAIXO"))) and st_d != "DESTINO_SUSPEITO":
                irmaos = [x for x, l in amb.lote_de.items() if x != tid and
                          amb.titulos.loc[x, "fornecedor_id"] == t.fornecedor_id and
                          amb.titulos.loc[x, "nfn"] == t.nfn and amb.titulos.loc[x, "pn"] == t.pn]
                soma = t.v + sum(amb.valor(x) for x in irmaos)
                if irmaos and abs(soma - info) <= 0.10 * (1 + len(irmaos)):
                    dec["acao"] = "fracionamento_unificado"
                    amb.abrir_incidente(self.nome, self.papel, "fracionamento", "operador", [tid] + irmaos,
                                        f"NF {t.nfn} parcela {t.pn} dividida em {1+len(irmaos)} títulos que somam "
                                        f"{brl(soma)}; alçada vale pela soma. Unificado em {tid}.")
                else:
                    dec["acao"] = "analista_corrige_abaixo"
                self.analista.corrigir_abaixo(t, chave, info, lote, self.livro)
            else:
                amb.retirar_do_lote(self.nome, self.papel, tid, f"livro={st_l}; destino={st_d}: {motivo_d}")
                if st_d == "DESTINO_SUSPEITO" and ev_d and "alteracao_id" in ev_d:
                    self.tratar_troca_bancaria(t, ev_d, motivo_d, lote)
                    dec["acao"] = "conta_revertida_e_reagendado"
                else:
                    dec["acao"] = self.analista.resolver(t, st_l, chave, info, st_d, motivo_d, lote, self.livro)
            self.decisoes.append(dec)
            self.decididos.add(tid)

    def tratar_troca_bancaria(self, t, ev, motivo, lote):
        fid = t.fornecedor_id
        if fid not in self.revertidos:
            evid = [f"alteração {ev['alteracao_id']} em {ev['data']} via {ev['origem']}: {ev['de']} -> {ev['para']}",
                    f"política: {motivo}"]
            self.amb.reverter_alteracao_cadastro(self.nome, self.papel, fid, ev["alteracao_id"], evid)
            self.amb.acionar_pessoa(self.nome, self.papel, "PS08",
                                    f"tentativa de golpe: troca de conta de {fid} sem confirmação válida", urgencia="alta",
                                    tipo="golpe", categoria=t.categoria)
            contatar_ou_escalar(self.amb, self.nome, self.papel, fid,
                                         "Recebemos pedido de troca de conta em seu nome. Mantivemos a conta anterior. "
                                         "Se a troca for legítima, confirme pelo telefone do cadastro.")
            self.revertidos.add(fid)
        # a obrigação é legítima: volta para o lote, agora pagando na conta confirmada
        st_l, chave, _ = self.livro.avaliar(t)
        if st_l == "OK":
            self.analista.reagendar(t.name if hasattr(t, "name") else t.titulo_id, lote,
                                    "obrigação aberta; cadastro revertido para conta confirmada")
            self.livro.consumir(chave, t.titulo_id)

    def apontar_causas(self):
        """Depois dos lotes: olha padrões nas exceções e abre incidente (causa raiz, não só o sintoma)."""
        exc = [d for d in self.decisoes if d["acao"] != "segue"]
        if not exc:
            return
        df = pd.DataFrame(exc)
        df["lancado_por"] = df.titulo_id.map(self.amb.titulos.lancado_por)
        df["origem"] = df.titulo_id.map(self.amb.titulos.origem_lancamento)
        dup = df[(df.livro == "JA_CONSUMIDA") & (df.acao == "cancelado_duplicidade")]
        if len(dup) >= 3:
            self.amb.abrir_incidente(self.nome, self.papel, "duplicidade", "processo",
                                     list(dup.titulo_id),
                                     f"{len(dup)} duplicidades; origens {dup.origem.value_counts().to_dict()}. "
                                     "Boleto recebido por e-mail é lançado como título novo mesmo quando a NF já "
                                     "veio pelo XML do portal.")
            self.amb._log(self.nome, "propor_correcao_processo", "enviado ao aprovador",
                          "Trava no ERP: lançamento a partir de boleto_email deve localizar o título da mesma "
                          "NF/parcela (raiz CNPJ + número normalizado) e só vincular a linha digitável.")
        por_user = df[df.livro.isin(["VALOR_ACIMA"]) | df.motivo_destino.str.contains("acima|ABAIXO", na=False)]
        for u, g in por_user.groupby("lancado_por"):
            if len(g) >= 2:
                self.amb.abrir_incidente(self.nome, self.papel, "erro_de_digitacao", "operador",
                                         list(g.titulo_id), f"{u} concentra {len(g)} erros de valor")
        # higiene de canal: fornecedores com títulos na fila cujo e-mail/telefone foi trocado sem confirmação
        from seguranca import canal_confiavel
        ativos = {d["fornecedor_id"] for d in self.decisoes}
        ac0 = self.amb.d["alteracoes_cadastro"]
        trocados = set(ac0[ac0.campo.isin(["email", "telefone"]) & (ac0.confirmacao_registrada != "sim")].fornecedor_id)
        sujos = sorted(f for f in trocados & ativos)
        if sujos:
            nc = ac0[ac0.campo.isin(["email", "telefone"]) & (ac0.confirmacao_registrada != "sim")]
            det = "; ".join(f"{f}: {r.campo} trocado em {r.data_hora[:10]} via {r.origem} sem confirmação, "
                            f"usar {canal_confiavel(self.amb.d, f)[0] or 'nenhum canal'}"
                            for f in sujos for _, r in nc[nc.fornecedor_id == f].iterrows())
            self.amb.abrir_incidente(self.nome, self.papel, "canal_nao_confirmado", "cadastro", [],
                                     f"{len(sujos)} fornecedores com canal de contato trocado sem confirmação. "
                                     f"O agente não usa esses canais até a confirmação por telefone anterior. {det}")
        ac = self.amb.d["alteracoes_cadastro"]
        for fid in self.revertidos:
            quem = ac[(ac.fornecedor_id == fid)].iloc[-1].alterado_por
            self.amb.abrir_incidente(self.nome, self.papel, "fraude_cadastro", "canal", [],
                                     f"troca bancária de {fid} gravada por {quem} a partir de e-mail, sem ligação "
                                     "de confirmação. Bloquear gravação de dados bancários sem confirmação no ERP.")


def contatar_ou_escalar(amb, ator, papel, fid, mensagem):
    """Se a ferramenta recusar (canal comprometido ou mensagem com dado sensível), não tenta outro caminho:
    aciona a coordenadora com o motivo."""
    try:
        amb.contatar_fornecedor(ator, papel, fid, mensagem)
        return True
    except AcaoNegada as e:
        amb.acionar_pessoa(ator, papel, "PS01", f"não contatei {fid}: {e}. Validar o canal do fornecedor "
                           "por telefone anterior antes de qualquer contato.", urgencia="alta", tipo="canal_comprometido")
        return False


class Analista:
    nome, papel = "agente_analista", "analista_cp"

    def __init__(self, amb):
        self.amb = amb

    def resolver_tres_vias(self, t, st_v, motivo):
        amb, tid = self.amb, t.titulo_id
        comprador = COMPRADOR_POR_CATEGORIA.get(t.categoria, "PS01")
        if st_v == "NF_INVALIDA":
            contatar_ou_escalar(self.amb, self.nome, self.papel, t.fornecedor_id,
                                    f"A NF {t.numero_nf_digitado} não está autorizada na SEFAZ. Há nota substituta?")
        amb.acionar_pessoa(self.nome, self.papel, comprador, f"{tid}: {motivo}", [tid],
                           tipo="tres_vias", categoria=t.categoria)
        return {"NF_INVALIDA": "retido_nf_invalida", "OC_CANCELADA": "retido_oc_cancelada",
                "NF_ACIMA_DA_OC": "retido_nf_acima_da_oc"}[st_v]

    def reagendar(self, tid, lote, motivo):
        try:
            self.amb.agendar_titulo(self.nome, self.papel, tid, lote)
        except AcaoNegada as e:
            self.amb._log(self.nome, "agendar_titulo", "NEGADA", str(e), tid)

    def corrigir_abaixo(self, t, chave, devido, lote, livro):
        tid = t.titulo_id
        self.amb.corrigir_lancamento(self.nome, self.papel, tid, "valor", round(devido, 2),
                                     "valor digitado abaixo da duplicata", [f"duplicata {chave} = {brl(devido)}"])
        if devido > 5000:   # alçada vale pelo valor que vai sair, não pela diferença corrigida
            self.amb.solicitar_aprovacao(self.nome, self.papel, "agendar_titulo", {"titulo_id": tid, "lote": lote},
                                         [f"duplicata {chave} = {brl(devido)}"], devido,
                                         motivo="valor corrigido para cima ultrapassa a alçada do analista")
        livro.consumir(chave, tid)

    def resolver(self, t, st_l, chave, info, st_d, motivo_d, lote, livro):
        amb, tid = self.amb, t.titulo_id
        if st_l == "JA_CONSUMIDA" and str(info).startswith("extrato"):
            amb.cancelar_titulo(self.nome, self.papel, tid, "já pago fora do lote, sem baixa no ERP",
                                [f"obrigação {chave} paga conforme {info}"])
            amb.acionar_pessoa(self.nome, self.papel, "PS02",
                               f"pagamento manual sem baixa no ERP: {tid} ({info}). Dar baixa no mesmo dia.", [tid],
                               tipo="pago_fora", categoria=t.categoria)
            return "cancelado_pago_fora_do_lote"
        if st_l == "JA_CONSUMIDA":
            amb.cancelar_titulo(self.nome, self.papel, tid, "duplicidade: obrigação já paga/reservada",
                                [f"obrigação {chave} já consumida pelo título {info}"])
            return "cancelado_duplicidade"

        if st_l == "VALOR_ACIMA" and st_d != "DESTINO_SUSPEITO":
            amb.corrigir_lancamento(self.nome, self.papel, tid, "valor", round(info, 2),
                                    f"valor digitado {brl(t.v)} acima da duplicata",
                                    [f"duplicata {chave} = {brl(info)}"])
            self.reagendar(tid, lote, "valor corrigido pela NF")
            livro.consumir(chave, tid)
            return "valor_corrigido_e_reagendado"

        if st_l == "SEM_OBRIGACAO":
            contatar_ou_escalar(self.amb, self.nome, self.papel, t.fornecedor_id,
                                    f"Não localizamos a NF {t.numero_nf_digitado} no portal. Pode reenviar o XML?")
            amb.acionar_pessoa(self.nome, self.papel, COMPRADOR_POR_CATEGORIA.get(t.categoria, "PS01"),
                               f"título {tid} sem NF/contrato correspondente", [tid],
                               tipo="sem_nf", categoria=t.categoria)
            return "retido_sem_obrigacao"

        # destino suspeito sem troca de cadastro (ex.: boleto de outro beneficiário)
        contatar_ou_escalar(self.amb, self.nome, self.papel, t.fornecedor_id,
                                "Recebemos um boleto com beneficiário diferente do seu cadastro. "
                                "Pode confirmar o boleto correto?")
        amb.acionar_pessoa(self.nome, self.papel, "PS01", f"suspeita de boleto adulterado: {motivo_d}",
                           [tid], urgencia="alta", tipo="boleto_suspeito", categoria=t.categoria)
        return "retido_destino_suspeito"
