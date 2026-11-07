"""Ambiente: as ferramentas do catálogo (ferramentas.csv) com papéis, alçadas e segregação aplicadas.

Nenhum agente mexe nos dados direto: tudo passa por uma ferramenta, que checa permissão e registra no log.
Humanos (aprovador, PS01, PS03...) são simulados: aprovam quando a solicitação traz evidência e
não é a própria pessoa aprovando a própria ação. Isso é uma premissa do MVP, não um resultado.
"""
from dataclasses import dataclass, field
import re
import pandas as pd
from seguranca import mascarar, checar_saida, canal_confiavel

ALCADA_ANALISTA = 5000.0
ALCADA_APROVADOR = 50000.0

PERMISSOES = {
    "corrigir_lancamento": {"analista_cp"},
    "agendar_titulo": {"analista_cp"},
    "retirar_do_lote": {"analista_cp", "auditor"},
    "cancelar_titulo": {"analista_cp"},
    "contatar_fornecedor": {"analista_cp", "auditor"},
    "bloquear_titulo": {"auditor"},
    "reverter_alteracao_cadastro": {"auditor"},
    "abrir_incidente": {"auditor", "aprovador"},
    "propor_correcao_processo": {"auditor"},
    "solicitar_aprovacao": {"analista_cp", "auditor"},
    "acionar_pessoa": {"analista_cp", "auditor", "aprovador"},
    "liberar_lote": {"aprovador"},
}


class AcaoNegada(Exception):
    pass


@dataclass
class Ambiente:
    d: dict
    log: list = field(default_factory=list)
    solicitacoes: list = field(default_factory=list)
    acionamentos: list = field(default_factory=list)
    pagos: list = field(default_factory=list)          # (titulo_id, lote, valor, destino)
    incidentes: list = field(default_factory=list)

    def __post_init__(self):
        cp = self.d["contas_a_pagar"].set_index("titulo_id")
        self.titulos = cp
        ag = self.d["agenda_remessa"].set_index("titulo_id")
        self.lote_de = ag.lote_remessa.to_dict()
        self.lote_original = dict(self.lote_de)
        self.destino_de = ag.destino_pagamento.to_dict()
        self.bloqueados = set()
        self.cancelados = set()
        self.cadastro = self.d["fornecedores"].set_index("fornecedor_id").copy()
        self.relogio = None
        self.ia = None     # IAFluxo opcional (roteamento e dossiê conferidos)
        # modo contínuo: aprovações levam tempo; o que depende delas espera (ver operacao.py)
        self.assincrono = False
        self.pend_tit, self.pend_forn = {}, {}   # título/fornecedor -> ids de pedidos ainda abertos
        self.aguardando = []                     # (ids de pedidos, efeito) a executar quando todos aprovarem
        self.segurados = {}                      # título segurado no fechamento -> lote original
        self.valor_futuro = {}                   # correção de valor aprovada-pendente
        self.lotes_fechados = set()
        self.agora = None

    # ---------------- infraestrutura
    def _checar(self, ferramenta, papel):
        if papel not in PERMISSOES[ferramenta]:
            self._log(papel, ferramenta, "NEGADA", f"papel {papel} não pode usar {ferramenta}")
            raise AcaoNegada(f"{papel} não pode {ferramenta}")

    def _log(self, ator, ferramenta, resultado, detalhe, titulo_id=None, evidencias=None):
        self.log.append({"quando": self.relogio, "ator": ator, "ferramenta": ferramenta,
                         "titulo_id": titulo_id, "resultado": resultado, "detalhe": mascarar(detalhe),
                         "evidencias": mascarar(evidencias)})

    def valor(self, tid):
        return float(self.titulos.loc[tid, "v"])

    # ---------------- aprovação (humanos simulados)
    def solicitar_aprovacao(self, ator, papel, acao, params, evidencias, valor, dupla=False, motivo=""):
        self._checar("solicitar_aprovacao", papel)
        if not evidencias:
            raise AcaoNegada("solicitação sem evidência")
        aprovadores = ["aprovador"]
        if valor > ALCADA_APROVADOR:
            aprovadores = ["aprovador", "PS03"]
        elif dupla:
            aprovadores = ["aprovador", "PS01"]
        sol = {"id": f"SOL{len(self.solicitacoes)+1:04d}", "solicitante": ator, "acao": acao,
               "params": params, "valor": valor, "aprovadores": aprovadores, "motivo": mascarar(motivo),
               "evidencias": mascarar(evidencias), "criada": self.agora,
               "decisao": "pendente" if self.assincrono else "aprovado"}   # premissa: humano aprova com evidência
        assert ator not in aprovadores, "segregação: quem solicita não aprova"
        modelo = (f"{acao} | valor R$ {valor:,.2f} | {params.get('titulo_id') or params.get('fornecedor_id') or ''} | "
                  f"{mascarar(motivo)} | evidência: {mascarar(str(evidencias[0])) if evidencias else ''}")
        sol["dossie"], sol["dossie_fonte"] = (self.ia.dossie(sol, modelo) if self.ia is not None else (modelo, "modelo fixo"))
        self.solicitacoes.append(sol)
        for p in aprovadores:
            self.acionamentos.append({"quando": self.relogio, "pessoa": p, "motivo": mascarar(f"{acao}: {motivo}"),
                                      "titulo_id": params.get("titulo_id"), "tipo": "aprovacao"})
        if self.assincrono:
            for t in ([params["titulo_id"]] if params.get("titulo_id") else []) + list(params.get("titulos", [])):
                self.pend_tit.setdefault(t, set()).add(sol["id"])
            if acao == "reverter_alteracao_cadastro":
                self.pend_forn.setdefault(params["fornecedor_id"], set()).add(sol["id"])
        self._log(ator, "solicitar_aprovacao", sol["decisao"], f"{acao} por {'+'.join(aprovadores)}: {motivo}",
                  params.get("titulo_id"), evidencias[:5] if isinstance(evidencias, list) else evidencias)
        return sol

    # ---------------- ferramentas de execução
    def retirar_do_lote(self, ator, papel, tid, motivo):
        self._checar("retirar_do_lote", papel)
        lote = self.lote_de.pop(tid, None)
        self._log(ator, "retirar_do_lote", "ok", f"{lote}: {motivo}", tid)

    def bloquear_titulo(self, ator, papel, tid, motivo, evidencias):
        self._checar("bloquear_titulo", papel)
        self.bloqueados.add(tid)
        self.lote_de.pop(tid, None)
        self._log(ator, "bloquear_titulo", "ok", motivo, tid, evidencias)

    def cancelar_titulo(self, ator, papel, tid, motivo, evidencias):
        self._checar("cancelar_titulo", papel)
        if not evidencias:
            raise AcaoNegada("cancelar exige evidência")
        v = self.valor(tid)
        if v > ALCADA_ANALISTA:
            self.solicitar_aprovacao(ator, papel, "cancelar_titulo", {"titulo_id": tid}, evidencias, v,
                                     motivo=motivo)
        self.lote_de.pop(tid, None)        # fora do lote já; o cancelamento formal espera a aprovação

        def efeito():
            self.cancelados.add(tid)
            self._log(ator, "cancelar_titulo", "ok", motivo, tid, evidencias)
        self.quando_aprovado(self.deps(tid), efeito)

    def corrigir_lancamento(self, ator, papel, tid, campo, valor_novo, justificativa, evidencias):
        self._checar("corrigir_lancamento", papel)
        if not evidencias:
            raise AcaoNegada("correção exige evidência")
        antigo = self.titulos.loc[tid, campo] if campo != "valor" else self.valor(tid)
        if campo == "valor" and abs(float(valor_novo) - antigo) > ALCADA_ANALISTA:
            self.solicitar_aprovacao(ator, papel, "corrigir_lancamento",
                                     {"titulo_id": tid, "campo": campo, "valor_novo": valor_novo},
                                     evidencias, abs(float(valor_novo) - antigo), motivo=justificativa)
        if campo == "valor":
            self.valor_futuro[tid] = float(valor_novo)   # para decisões seguintes enquanto a correção espera aprovação

        def efeito():
            if campo == "valor":
                self.titulos.loc[tid, "v"] = float(valor_novo)
                self.valor_futuro.pop(tid, None)
            self.titulos.loc[tid, campo] = str(valor_novo)
            self._log(ator, "corrigir_lancamento", "ok", f"{campo}: {antigo} -> {valor_novo} ({justificativa})",
                      tid, evidencias)
        self.quando_aprovado(self.deps(tid), efeito)

    def agendar_titulo(self, ator, papel, tid, lote):
        self._checar("agendar_titulo", papel)
        if tid in self.bloqueados or tid in self.cancelados:
            raise AcaoNegada("título bloqueado/cancelado")
        v = self.valor_futuro.get(tid, self.valor(tid))   # valor que vai sair, já corrigido
        if v > ALCADA_ANALISTA:
            self.solicitar_aprovacao(ator, papel, "agendar_titulo", {"titulo_id": tid, "lote": lote},
                                     ["obrigação aberta e destino confiável"], v,
                                     motivo="reagendar após correção")
        def efeito():
            destino = lote if lote not in self.lotes_fechados else self.proximo_lote(tid)
            if tid in self.cancelados or destino is None:
                return
            self.segurados.pop(tid, None)
            self.lote_de[tid] = destino
            self._log(ator, "agendar_titulo", "ok", destino + ("" if destino == lote else f" (era {lote}: atraso)"), tid)
        self.quando_aprovado(self.deps(tid), efeito)

    def reverter_alteracao_cadastro(self, ator, papel, fid, alteracao_id, evidencias):
        self._checar("reverter_alteracao_cadastro", papel)
        self.solicitar_aprovacao(ator, papel, "reverter_alteracao_cadastro",
                                 {"fornecedor_id": fid, "alteracao_id": alteracao_id}, evidencias, 0,
                                 dupla=True, motivo="troca bancária sem confirmação válida")
        self.quando_aprovado(self.pend_forn.get(fid, set()) | set(),
                             lambda: self._efeito_reverter(ator, fid, alteracao_id, evidencias))

    def _efeito_reverter(self, ator, fid, alteracao_id, evidencias):
        ac = self.d["alteracoes_cadastro"]
        linhas = ac[(ac.fornecedor_id == fid) & (ac.data_hora == ac.loc[ac.alteracao_id == alteracao_id, "data_hora"].iloc[0])]
        for _, l in linhas.iterrows():
            self.cadastro.loc[fid, l.campo] = l.valor_anterior
        f = self.cadastro.loc[fid]
        novo_destino = f"ted {f.banco}/{f.agencia}/{f.conta}"
        for tid, lote in self.lote_original.items():
            if self.titulos.loc[tid, "fornecedor_id"] != fid:
                continue
            forma = self.titulos.loc[tid, "forma_pagamento"]
            if forma == "ted":
                self.destino_de[tid] = novo_destino
            elif forma == "pix":
                self.destino_de[tid] = f"pix {f.chave_pix}"
        self._log(ator, "reverter_alteracao_cadastro", "ok", f"{fid} volta para {novo_destino}", None, evidencias)

    def contatar_fornecedor(self, ator, papel, fid, mensagem):
        """Só sai por canal confiável e sem dado bancário ou de terceiro (seguranca.py)."""
        self._checar("contatar_fornecedor", papel)
        f = self.cadastro.loc[fid]
        canal, motivo = canal_confiavel(self.d, fid)
        if canal is None:
            self._log(ator, "contatar_fornecedor", "NEGADA", f"{fid}: nenhum canal confiável ({motivo})")
            raise AcaoNegada(f"canal comprometido: {motivo}")
        raiz = re.sub(r"\D", "", str(f.cnpj))[:8]
        prob = checar_saida(mensagem, raiz)
        if prob:
            self._log(ator, "contatar_fornecedor", "NEGADA", f"{fid}: mensagem bloqueada por conter {', '.join(prob)}")
            raise AcaoNegada(f"mensagem com {', '.join(prob)}")
        destino = f.email_cadastrado if canal == "email" else f.telefone_cadastrado
        self._log(ator, "contatar_fornecedor", "ok", f"via {canal} confiável {destino} ({motivo}): {mensagem}")

    def acionar_pessoa(self, ator, papel, pessoa_id, motivo, titulos=None, urgencia="normal", tipo=None, categoria=None):
        """pessoa_id é a escolha da regra. Com IA ligada e 'tipo' informado, a IA propõe e a política confere."""
        self._checar("acionar_pessoa", papel)
        fonte = "regra"
        if self.ia is not None and tipo:
            pessoa_id, urg_ia, fonte = self.ia.rotear(tipo, motivo, pessoa_id, categoria)
            if urgencia == "normal":
                urgencia = urg_ia
        self.acionamentos.append({"quando": self.relogio, "pessoa": pessoa_id, "motivo": mascarar(motivo),
                                  "titulo_id": ",".join(titulos or []), "tipo": "aviso", "roteado_por": fonte})
        self._log(ator, "acionar_pessoa", "ok", f"{pessoa_id} [{urgencia}] ({fonte}): {motivo}")

    def abrir_incidente(self, ator, papel, tipo, causa, titulos, descricao):
        self._checar("abrir_incidente", papel)
        self.incidentes.append({"tipo": tipo, "causa": causa, "titulos": titulos, "descricao": descricao})
        self._log(ator, "abrir_incidente", "ok", f"{tipo} / {causa}: {descricao}")

    # ---------------- fechamento do lote (humano assina; o que está no lote sai)
    def fechar_lote(self, lote):
        for t in [t for t, l in self.lote_de.items() if l == lote and self.deps(t)]:
            del self.lote_de[t]                      # padrão seguro: sem aprovação, não sai
            self.segurados[t] = lote
            self._log("tesoureiro", "liberar_lote", "segurado", f"{lote}: aguardando aprovação {sorted(self.deps(t))}", t)
        self.lotes_fechados.add(lote)
        saem = [t for t, l in self.lote_de.items() if l == lote]
        for t in saem:
            self.pagos.append({"titulo_id": t, "lote": lote, "valor": self.valor(t), "destino": self.destino_de.get(t)})
            del self.lote_de[t]
        self._log("tesoureiro", "liberar_lote", "ok", f"{lote}: {len(saem)} títulos, "
                  f"R$ {sum(self.valor(t) for t in saem):,.2f}")
        return saem

    # ---------------- modo contínuo
    def deps(self, tid):
        """Pedidos ainda abertos que seguram este título (dele ou de reversão do fornecedor)."""
        fid = self.titulos.loc[tid, "fornecedor_id"] if tid in self.titulos.index else None
        return set(self.pend_tit.get(tid, set())) | set(self.pend_forn.get(fid, set()))

    def quando_aprovado(self, deps, efeito):
        if not deps:
            efeito()
        else:
            self.aguardando.append((set(deps), efeito))

    def proximo_lote(self, tid):
        ag = self.d["agenda_remessa"]
        banco = ag.loc[ag.titulo_id == tid, "banco_pagador"]
        banco = banco.iloc[0] if len(banco) else "Banco Meridiano"
        cand = ag[(ag.banco_pagador == banco) & ~ag.lote_remessa.isin(self.lotes_fechados)].sort_values("data_prevista")
        return None if cand.empty else cand.lote_remessa.iloc[0]

    def aprovar(self, sol_id, quando, por):
        sol = next(s for s in self.solicitacoes if s["id"] == sol_id)
        if sol["decisao"] != "pendente":
            return
        sol["decisao"], sol["aprovada_em"], sol["aprovada_por"] = "aprovado", quando, por
        self._log("+".join(por), "aprovar_acao", "ok", f"{sol_id} ({sol['acao']})", sol["params"].get("titulo_id"))
        for d in (self.pend_tit, self.pend_forn):
            for k in list(d):
                d[k].discard(sol_id)
                if not d[k]:
                    del d[k]
        prontos = [(dp, ef) for dp, ef in self.aguardando if not (dp & self._abertos())]
        self.aguardando = [(dp, ef) for dp, ef in self.aguardando if dp & self._abertos()]
        for _, ef in prontos:
            ef()
        for t, lote in list(self.segurados.items()):   # o que foi segurado volta no próximo lote aberto
            if not self.deps(t) and t not in self.cancelados and t not in self.lote_de:
                novo = self.proximo_lote(t)
                if novo:
                    self.segurados.pop(t)
                    self.lote_de[t] = novo
                    self._log("agente_analista", "agendar_titulo", "ok", f"{novo} (segurado em {lote}: atraso)", t)

    def _abertos(self):
        return {s["id"] for s in self.solicitacoes if s["decisao"] == "pendente"}
