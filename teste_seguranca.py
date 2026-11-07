"""Testes da etapa de segurança. Escritos ANTES da implementação (seguranca.py).
Princípio: nem dinheiro nem dado saem sem motivo. O controle fica na camada de ferramentas
(Ambiente), não na boa vontade do agente.
"""
import re
import pandas as pd

from livro import carregar
from ambiente import Ambiente, AcaoNegada

LD = re.compile(r"\d{47}")
CONTA = re.compile(r"\b\d{6,8}-\d\b")
RES = []


def ok(nome, cond, detalhe=""):
    RES.append((nome, bool(cond)))
    print(("✅ " if cond else "❌ ") + nome + (f"  [{detalhe}]" if detalhe and not cond else ""))


def t_canal():
    d = carregar()
    amb = Ambiente(d)
    # FP9180: e-mail trocado em 26/10 sem confirmação (gmail genérico) e telefone sem troca
    from seguranca import canal_confiavel
    canal, motivo = canal_confiavel(d, "FP9180")
    ok("Canal: FP9180 não usa o e-mail trocado sem confirmação", canal != "email", motivo)
    # FP6572 (aluguel CD1): e-mail trocado sem confirmação
    canal, motivo = canal_confiavel(d, "FP6572")
    ok("Canal: FP6572 não usa o e-mail trocado sem confirmação", canal != "email", motivo)
    # fornecedor sem nenhuma troca: e-mail do cadastro é confiável
    canal, _ = canal_confiavel(d, "FP1103")
    ok("Canal: fornecedor sem troca usa o e-mail do cadastro", canal == "email")
    # FP2303: telefone trocado e confirmado por e-mail do domínio cadastrado -> e-mail segue confiável
    canal, _ = canal_confiavel(d, "FP2303")
    ok("Canal: troca confirmada não bloqueia o contato", canal is not None)
    # e-mail E telefone comprometidos -> ferramenta recusa e nada é enviado
    ac = d["alteracoes_cadastro"]
    ac.loc[len(ac)] = {"alteracao_id": "AC00001", "data_hora": "2026-10-27 10:00:00", "fornecedor_id": "FP9180",
                       "campo": "telefone", "valor_anterior": "(11) 1111-1111", "valor_novo": "(11) 99999-9999",
                       "alterado_por": "usuario_03", "origem": "whatsapp", "confirmacao_registrada": "não",
                       "confirmacao_como": None}
    amb = Ambiente(d)
    try:
        amb.contatar_fornecedor("agente_analista", "analista_cp", "FP9180", "Pode reenviar o XML da NF 123?")
        ok("Canal: e-mail e telefone comprometidos -> ferramenta recusa o contato", False)
    except AcaoNegada:
        enviados = [l for l in amb.log if l["ferramenta"] == "contatar_fornecedor" and l["resultado"] == "ok"]
        ok("Canal: e-mail e telefone comprometidos -> ferramenta recusa o contato", not enviados)


def t_saida():
    d = carregar()
    amb = Ambiente(d)
    fo = d["fornecedores"].set_index("fornecedor_id")
    outro_cnpj = fo.loc["FP4615", "cnpj"]
    casos = [
        ("Saída: CNPJ de outro fornecedor é bloqueado",
         f"Segue a relação: Embalarte {outro_cnpj} também está em aberto."),
        ("Saída: linha digitável é bloqueada",
         "Linha: 28995217401010503090100001158617316080003002303"),
        ("Saída: número de conta é bloqueado", "Pagamos na conta 4471203-9, agência 0001."),
        ("Saída: chave pix é bloqueada", "A chave pix cadastrada é 244c83bc-a736-4fff-afa2-6e12fdcf5242."),
    ]
    for nome, msg in casos:
        try:
            amb.contatar_fornecedor("agente_analista", "analista_cp", "FP1103", msg)
            ok(nome, False, "mensagem saiu")
        except AcaoNegada:
            ok(nome, True)
    try:
        amb.contatar_fornecedor("agente_analista", "analista_cp", "FP1103",
                                "Não localizamos a NF 49704 no portal. Pode reenviar o XML?")
        ok("Saída: mensagem legítima com número de NF passa", True)
    except AcaoNegada as e:
        ok("Saída: mensagem legítima com número de NF passa", False, str(e))


def t_mascaramento():
    from seguranca import mascarar
    s = mascarar("conta 4471203-9 ag 0001 linha 28995217401010503090100001158617316080003002303 "
                 "pix 244c83bc-a736-4fff-afa2-6e12fdcf5242 cpf 123.456.789-09")
    ok("Máscara: conta, linha digitável, pix e CPF somem", not CONTA.search(s) and not LD.search(s)
       and "244c83bc" not in s and "123.456.789-09" not in s, s)
    from rodar import rodar
    amb, conc = rodar(carregar())
    log = pd.DataFrame(amb.log).fillna("").astype(str)
    texto = " ".join(log.detalhe) + " ".join(log.evidencias)
    ok("Logs: nenhuma conta ou linha digitável completa", not CONTA.search(texto) and not LD.search(texto),
       (CONTA.search(texto) or LD.search(texto) or [""])[0])
    sol = " ".join(str(s["evidencias"]) + str(s["motivo"]) for s in amb.solicitacoes)
    ok("Pedidos de aprovação: dados bancários mascarados", not CONTA.search(sol) and not LD.search(sol))


def t_triagem():
    import ia
    d = carregar()
    cm, fo = d["comunicacoes_fornecedores"].fillna(""), d["fornecedores"]
    pig = cm[cm.remetente.str.contains("outlook", na=False)].iloc[0]
    f = ia.fornecedor_do_email(pig, fo)
    ok("Triagem: segunda via do @outlook ligada à Pigmentos Aquarela (FP4345)",
       f is not None and f.fornecedor_id == "FP4345")
    estados = [ia.estado_para_jev(e, ia.fornecedor_do_email(e, fo)) for _, e in cm.iterrows()]
    txt = " ".join(str(s) for s in estados)
    ok("Triagem: nada enviado ao Jev contém conta ou linha digitável completa",
       not CONTA.search(txt) and not LD.search(txt))
    ok("Triagem: Jev recebe só os campos necessários",
       all(set(s) <= {"remetente", "nome_remetente", "assunto", "texto", "anexos", "email_do_cadastro"} for s in estados))


def t_regressao():
    from rodar import rodar
    amb, conc = rodar(carregar())
    dec = pd.DataFrame(conc.decisoes)
    ok("Regressão: fila real continua com 847 títulos seguindo sozinhos", (dec.acao == "segue").sum() == 847,
       str((dec.acao == "segue").sum()))
    pagos = pd.DataFrame(amb.pagos)
    ok("Regressão: nada da Embalarte sai para a conta 905", not pagos.destino.str.contains("905/0001", na=False).any())


if __name__ == "__main__":
    for t in (t_canal, t_saida, t_mascaramento, t_triagem, t_regressao):
        try:
            t()
        except Exception as e:
            ok(f"{t.__name__} executou", False, f"{type(e).__name__}: {e}")
    print(f"\n{sum(r for _, r in RES)}/{len(RES)} testes passaram")
