"""Checagem de saída v2 — testes escritos ANTES de mexer em seguranca.py.
Dois lados: o que tem que ser bloqueado e o que tem que passar (falso positivo também custa:
o agente deixaria de falar com o fornecedor).
"""
from seguranca import checar_saida, mascarar

RAIZ = "34030017"   # destinatário: Vitrine (FP1103)

BLOQUEAR = [
    ("conta com 5 dígitos e contexto", "Depositar na c/c 12345-6, ag. 0001."),
    ("conta com 10 dígitos", "Conta corrente 1234567890-1 no banco 289."),
    ("conta sem traço com contexto", "conta: 98765432 agência 1234"),
    ("agência e conta por extenso", "Agência 4521, conta 7788990-1"),
    ("chave pix em formato de e-mail", "Nossa chave pix: financeiro.novo@gmail.com"),
    ("chave pix telefone", "chave PIX (85) 99999-0000"),
    ("linha digitável com pontos e espaços", "28995.21740 10105.030901 00001.158617 3 16080003002303"),
    ("CPF sem pontuação", "CPF 12345678909 do titular"),
    ("CNPJ de terceiro sem pontuação", "Embalarte 85990602000196 também está em aberto"),
]
PASSAR = [
    ("NF com série", "Não localizamos a NF 11079-1 no portal. Pode reenviar o XML?"),
    ("parcela e valor", "A parcela 3/3 de R$ 4.838,00 vence em 05/11/2026."),
    ("OC", "O pedido OC-36184 está cancelado em Compras."),
    ("telefone do próprio fornecedor sem contexto de pix", "Se preferir, ligue para (41) 3712-1622."),
    ("CNPJ do próprio destinatário", "Título do CNPJ 34.030.017/0001-03 em análise."),
    ("NF longa com zeros", "Recebemos a NF 000036431, obrigado."),
    ("e-mail de contato sem pix", "Responda para contasapagar@gogroup.com.br."),
]


def main():
    ok = 0
    for nome, msg in BLOQUEAR:
        r = bool(checar_saida(msg, RAIZ))
        ok += r
        print(("✅ bloqueou: " if r else "❌ deixou passar: ") + nome)
    for nome, msg in PASSAR:
        r = not checar_saida(msg, RAIZ)
        ok += r
        print(("✅ passou: " if r else "❌ bloqueou à toa: ") + nome + ("" if r else f"  {checar_saida(msg, RAIZ)}"))
    m = mascarar("Agência 4521, conta 7788990-1; c/c 12345-6; pix financeiro.novo@gmail.com")
    r = "4521" not in m and "7788990" not in m and "12345-6" not in m and "financeiro.novo" not in m
    ok += r
    print(("✅ " if r else "❌ ") + "máscara cobre agência, conta curta e pix e-mail  " + ("" if r else m))
    # todas as mensagens reais que os agentes mandaram para fornecedores precisam continuar passando
    from livro import carregar
    from rodar import rodar
    amb, _ = rodar(carregar())
    enviados = [l for l in amb.log if l["ferramenta"] == "contatar_fornecedor"]
    negados = [l for l in enviados if l["resultado"] == "NEGADA"]
    r = len(negados) == 0 and len(enviados) > 0
    ok += r
    print(("✅ " if r else "❌ ") + f"mensagens reais dos agentes: {len(enviados) - len(negados)}/{len(enviados)} passaram")
    total = len(BLOQUEAR) + len(PASSAR) + 2
    print(f"\n{ok}/{total} testes passaram")


if __name__ == "__main__":
    main()
