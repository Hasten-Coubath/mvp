"""Gera as conversas do Mensageiro (simulação) a partir do log real dos agentes e monta o HTML."""
import json
import re
import sys
import pandas as pd
from livro import carregar
from rodar import rodar

PESSOAS = {'aprovador': ('Cláudia Ferraz', 'Supervisora de contas a pagar'), 'PS01': ('Renata Paiva', 'Coordenadora de contas a pagar'),
           'PS02': ('Marcos Teles', 'Tesoureiro'), 'PS03': ('Helena Duarte', 'Diretora financeira'),
           'PS04': ('Carla Menezes', 'Compradora · produto e embalagens'), 'PS05': ('Diego Prado', 'Coordenador de compras'),
           'PS06': ('Bruno Sá', 'Gerente de marketing'), 'PS07': ('Fernanda Rocha', 'Gerente de logística'),
           'PS08': ('Lívia Campos', 'Advogada · jurídico'), 'PS09': ('Rafael Nunes', 'Controller'),
           'PS10': ('Tiago Lins', 'Coordenador de facilities'), 'PS11': ('Paula Viana', 'Coordenadora de TI')}
ACAO = {'cancelar_titulo': 'Cancelar título', 'corrigir_lancamento': 'Corrigir valor', 'agendar_titulo': 'Liberar pagamento',
        'reverter_alteracao_cadastro': 'Reverter troca de conta'}
RESPOSTAS = {'F:FP4615': 'Bom dia. Não pedimos troca de conta. Nossa conta continua a mesma. Obrigada pelo aviso! (financeiro Embalarte)',
             'F:FP4345': 'Olá! Esse boleto não foi emitido por nós. Vamos enviar o boleto correto pelo banco habitual.',
             'F:FP5079': 'Vamos reenviar o XML da NF 49704 ainda hoje.',
             'F:FP3614': 'O XML da NF 36431 foi reenviado ao portal agora há pouco.'}


def brl(v):
    return f"R$ {v:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')


def gerar(usar_ia=False):
    d = carregar()
    amb, conc = rodar(d, usar_ia=usar_ia)
    fo, tit = d['fornecedores'].set_index('fornecedor_id'), amb.titulos
    ev, n = [], [0]

    def add(chat, autor, texto, quando, **k):
        n[0] += 1
        ev.append(dict(id=n[0], chat=chat, autor=autor, texto=texto, quando=quando, **k))

    def desc(tid):
        if not isinstance(tid, str):
            return ''
        t = tit.loc[tid]
        return f"{tid} · {fo.loc[t.fornecedor_id].nome_fantasia} · NF {t.numero_nf_digitado}"

    sols = iter(amb.solicitacoes)
    for l in amb.log:
        q, f = l['quando'], l['ferramenta']
        tid = l['titulo_id'] if isinstance(l['titulo_id'], str) else None
        ag = 'Conciliador' if l['ator'] == 'agente_conciliador' else 'Analista'
        if f == 'retirar_do_lote':
            add('equipe', 'Conciliador', f"Tirei {desc(tid)} do lote. Motivo: {l['detalhe'].split(': ', 1)[-1]}", q)
        elif f == 'solicitar_aprovacao':
            s = next(sols)
            p = s['params']
            if p.get('titulo_id'):
                alvo = desc(p['titulo_id'])
            elif p.get('titulos'):
                alvo = f"{len(p['titulos'])} títulos em aberto (pedido único)"
            else:
                alvo = f"{fo.loc[p['fornecedor_id']].nome_fantasia} ({p['fornecedor_id']})"
            for ap in s['aprovadores']:
                fonte = s.get('dossie_fonte', 'modelo fixo')
                texto = (s['dossie'] + "  [redigido pela IA e conferido]") if fonte == 'ia' else \
                    f"Preciso da sua aprovação: {ACAO.get(s['acao'], s['acao'])}."
                add(ap, 'Conciliador' if s['solicitante'] == 'agente_conciliador' else 'Analista', texto, q,
                    pedido=dict(acao=ACAO.get(s['acao'], s['acao']), alvo=alvo, valor=brl(s['valor']) if s['valor'] else '—',
                                motivo=s['motivo'], evidencias=[str(e) for e in s['evidencias']][:3],
                                dupla=len(s['aprovadores']) > 1, outros=[PESSOAS[x][0] for x in s['aprovadores'] if x != ap]))
        elif f == 'acionar_pessoa':
            pid, txt = l['detalhe'].split(' ')[0], l['detalhe'].split(': ', 1)[1]
            rot = ' [IA escolheu quem acionar; conferido pela política]' if '(ia)' in l['detalhe'] else ''
            add(pid, ag, txt[0].upper() + txt[1:] + rot, q, aviso=True, urgente='[alta]' in l['detalhe'])
        elif f == 'contatar_fornecedor' and l['resultado'] == 'ok':
            m = re.match(r"via (\w+) confiável (\S+(?: \S+)?) \((.*?)\): (.*)", l['detalhe'])
            canal, destino, motivo, msg = m.groups()
            fid = fo.index[(fo.email_cadastrado == destino) | (fo.telefone_cadastrado == destino)][0]
            add('F:' + fid, ag, msg, q, canal=f"{canal} {destino} · {motivo}")
        elif f == 'contatar_fornecedor':
            add('equipe', ag, f"Contato bloqueado pela camada de segurança: {l['detalhe']}", q, bloqueio=True)
        elif f in ACAO:
            verbo = {'cancelar_titulo': 'Cancelei', 'corrigir_lancamento': 'Corrigi', 'agendar_titulo': 'Reagendei',
                     'reverter_alteracao_cadastro': 'Reverti o cadastro de'}[f]
            det = l['detalhe'] if f != 'agendar_titulo' else f"no lote {l['detalhe']}"
            add('equipe', ag, f"{verbo} {desc(tid) if tid else ''} {det}".replace('  ', ' '), q, feito=True)
        elif f == 'liberar_lote':
            lote, resto = l['detalhe'].split(': ', 1)
            exc = sum(1 for x in conc.decisoes if x['lote'] == lote and x['acao'] != 'segue')
            add('PS02', 'Conciliador', f"Lote {lote} conferido título a título: {resto}. {exc} exceção(ões) tratada(s) "
                "antes do fechamento. Pode assinar.", q, lote=lote)
        elif f in ('abrir_incidente', 'propor_correcao_processo'):
            add('PS01', 'Conciliador', ('Incidente aberto. ' if f == 'abrir_incidente' else 'Proposta de correção de processo: ')
                + l['detalhe'], q, incidente=True)
    chats = {'equipe': dict(nome='Conciliador + Analista', sub='Grupo dos agentes', tipo='agentes')}
    for k, (nome, cargo) in PESSOAS.items():
        chats[k] = dict(nome=nome, sub=cargo, tipo='pessoa')
    for e in ev:
        if e['chat'].startswith('F:'):
            chats[e['chat']] = dict(nome=fo.loc[e['chat'][2:]].nome_fantasia, sub='Fornecedor · só canal confirmado', tipo='fornecedor')
    chats = {k: v for k, v in chats.items() if any(e['chat'] == k for e in ev)}
    dec = pd.DataFrame(conc.decisoes)
    resumo = dict(titulos=int(len(dec)), seguiram=int((dec.acao == 'segue').sum()), excecoes=int((dec.acao != 'segue').sum()),
                  acionamentos=len(amb.acionamentos))
    return dict(chats=chats, eventos=ev, resumo=resumo, respostas_fornecedor=RESPOSTAS)


if __name__ == "__main__":
    D = gerar(usar_ia="--ia" in sys.argv)
    html = open('mensageiro_modelo.html', encoding='utf-8').read().replace('__DADOS__', json.dumps(D, ensure_ascii=False, default=str).replace('</', '<\\/'))
    saida = next((a for a in sys.argv[1:] if not a.startswith('--')), 'mensageiro_pronto.html')
    open(saida, 'w', encoding='utf-8').write(html)
    print(len(D['eventos']), 'mensagens')
