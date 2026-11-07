"""Placar conservador da fila real (03/11 a 13/11), pela fórmula do parametros.md:
placar = -(dinheiro que saiu indevidamente + custo de atrasar legítimo + R$ 60 por acionamento desnecessário)

Sem gabarito, o que o sistema barrou é separado por grau de prova:
  A. Comprovado por documento: a própria base prova que o pagamento seria indevido.
  B. Evidência forte de golpe: sinais objetivos, mas a prova final é a confirmação do fornecedor.
  C. Aguardando confirmação: pode ser erro ou pode ser legítimo atrasado.
Para o custo, usamos o pior caso: todo acionamento conta como desnecessário e todo item de C é legítimo.
"""
import pandas as pd
from livro import carregar
from rodar import rodar
from teste_cego import custo_atraso


def calcular():
    d = carregar()
    amb, conc = rodar(d)
    dec = pd.DataFrame(conc.decisoes)
    tit = amb.titulos
    orig = d["contas_a_pagar"].set_index("titulo_id").v
    linhas = []
    for _, r in dec[dec.acao != "segue"].iterrows():
        a, tid, v0 = r.acao, r.titulo_id, float(orig[r.titulo_id])
        if a == "cancelado_duplicidade_em_aberto":
            linhas.append(("A. Comprovado", "Duplicidade em aberto, fora da janela (vence 16/11 a 14/01)", tid, v0))
        elif a == "cancelado_duplicidade":
            linhas.append(("A. Comprovado", "Duplicidade (obrigação já paga ou reservada)", tid, v0))
        elif a == "cancelado_pago_fora_do_lote":
            linhas.append(("A. Comprovado", "Já pago por fora do lote (está no extrato)", tid, v0))
        elif a == "valor_corrigido_e_reagendado":
            linhas.append(("A. Comprovado", "Excesso de valor sobre a NF (zero a mais)", tid, v0 - float(tit.loc[tid, "v"])))
        elif a in ("conta_revertida_e_reagendado",) or (r.fornecedor_id in conc.revertidos and a == "segue"):
            pass
        elif a == "retido_destino_suspeito":
            linhas.append(("B. Evidência forte de golpe", "Boleto com CNPJ de outra empresa", tid, v0))
        elif a in ("retido_sem_obrigacao", "retido_oc_cancelada", "retido_nf_invalida", "retido_nf_acima_da_oc"):
            linhas.append(("C. Aguardando confirmação", a.replace("retido_", "Retido: ").replace("_", " "), tid, v0))
    # Embalarte: todos os TED/PIX do fornecedor revertido teriam ido para a conta trocada
    ag = d["agenda_remessa"]
    for fid in conc.revertidos:
        for tid in ag[(ag.fornecedor_id == fid) & ag.forma_pagamento.isin(["ted", "pix"])].titulo_id:
            linhas.append(("B. Evidência forte de golpe", "Conta trocada sem confirmação (pagos em dia na conta anterior)",
                           tid, float(orig[tid])))
    L = pd.DataFrame(linhas, columns=["grau", "motivo", "titulo_id", "valor_evitado"])
    corr_abaixo = dec[dec.acao == "analista_corrige_abaixo"]
    pior_atraso = sum(custo_atraso(float(orig[t]), tit.loc[t, "categoria"]) for t in L[L.grau.str.startswith("C")].titulo_id)
    pior_acion = 60 * len(amb.acionamentos)
    return L, corr_abaixo, pior_atraso, pior_acion, amb


if __name__ == "__main__":
    L, abaixo, atraso, acion, amb = calcular()
    fmt = lambda v: f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    print("DINHEIRO QUE TERIA SAÍDO INDEVIDAMENTE SEM OS AGENTES\n")
    for grau, g in L.groupby("grau"):
        print(f"{grau}: {fmt(g.valor_evitado.sum())}")
        for motivo, gg in g.groupby("motivo"):
            print(f"    {motivo}: {len(gg)} título(s), {fmt(gg.valor_evitado.sum())}")
    A = L[L.grau.str.startswith("A")].valor_evitado.sum()
    AB = L[~L.grau.str.startswith("C")].valor_evitado.sum()
    print(f"\nCUSTOS NO PIOR CASO\n    atraso, se todo item de C for legítimo: {fmt(atraso)}"
          f"\n    acionamentos, se todos forem desnecessários: {fmt(acion)} ({len(amb.acionamentos)} x R$ 60)")
    print(f"\nFAIXA HONESTA DO RESULTADO LÍQUIDO")
    print(f"    conservador (só A, custos no pior caso): {fmt(A - atraso - acion)}")
    print(f"    provável (A + B, custos no pior caso):   {fmt(AB - atraso - acion)}")
    print(f"\nFora do placar: {len(abaixo)} títulos com 'zero a menos' corrigidos (evita cobrança e multa do fornecedor)")
    L.to_csv("saida_placar.csv", index=False)
