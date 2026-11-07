# Motivo: nenhum real sai sem motivo

**Motivo** são dois agentes de IA de contas a pagar, feitos para o desafio **Pagamentos** do GoHacks Talent 2026.

> Dois agentes conferem cada pagamento antes de o lote fechar e só deixam sair o que tem uma dívida real, ainda aberta e com destino confiável. A IA lê os e-mails dos fornecedores, sugere quem acionar e redige os pedidos de aprovação, sempre conferida pela política. Nenhuma IA libera dinheiro.

## O problema

Toda manhã o tesoureiro assina um lote com centenas de títulos sem conseguir conferir um por um: o que estiver no lote sai. Os títulos são digitados à mão a partir da NF do portal e do boleto que chega por e-mail, às vezes duas vezes.

No histórico da base (agosto a outubro de 2026), **cerca de R$ 450 mil saíram indevidamente**: 55 NFs pagas em duplicidade e um pagamento com zero a mais.

## Como funciona

Antes de cada fechamento de lote, cada título responde a três perguntas:

1. **Existe uma dívida real?** Uma duplicata de NF autorizada na SEFAZ ou uma parcela de contrato vigente, com OC aprovada e NF dentro da tolerância da OC (conferência de 3 vias).
2. **Ela ainda está aberta?** Cada obrigação só pode ser paga uma vez. Pagamentos já feitos, inclusive os manuais que aparecem só no extrato, consomem a obrigação. Por isso a duplicidade é impossível por construção.
3. **O destino é confiável?** Uma troca de conta só vale com ligação ao telefone que estava no cadastro antes da troca. O boleto precisa ter o CNPJ, o banco e a conta de cobrança habituais do fornecedor.

### Os dois agentes

| Agente | Papel (catálogo de ferramentas) | O que faz sozinho | Quando chama alguém |
|---|---|---|---|
| **Conciliador** | `auditor` | Mantém o Livro de Obrigações; revisa cada lote; tira do lote o que não tem motivo; reverte cadastro fraudado; varre duplicatas em aberto; abre incidentes com a causa raiz | Golpe (jurídico), canal comprometido (coordenadora) |
| **Analista** | `analista_cp` | Cancela duplicidade, corrige valor pela NF, reagenda, fala com o fornecedor pelo canal confirmado | Acima da alçada (R$ 5 mil) e casos de 3 vias (comprador) |

**Operação contínua** (`operacao.py`): os agentes acompanham a semana hora a hora. Revisam cada lote às 16h do dia útil anterior, conferem quem respondeu, escalam para o nível de cima após 2h úteis sem resposta e, no fechamento, **seguram** o que ainda depende de aprovação. O título segurado volta no próximo lote quando for aprovado.

**Humanos** aprovam acima da alçada, aprovam em dupla trocas bancárias e pagamentos sem OC, e assinam o lote. Toda ação passa por uma camada de ferramentas (`ambiente.py`) que aplica papel, alçada, aprovação dupla e segregação. O agente não consegue contorná-la.

### Onde entra a IA (AI Proxy do Gogroup)

| Uso | Modelo | Conferência antes de usar |
|---|---|---|
| Triagem de cada e-mail: tipo do pedido, tentativa de pular controle, domínio suspeito | Jev (`/v1/systemone`) | A IA só **aperta** controles: o fornecedor entra em verificação. Confiança abaixo de 0,7 vai para um humano. |
| Quem acionar e com qual urgência em cada exceção | Jev | A pessoa precisa estar entre as permitidas pela política para aquele tipo de caso, com confiança de pelo menos 0,7; senão vale a regra. A IA nunca escolhe aprovador. |
| Redação do pedido de aprovação | Chat assíncrono (`/v1/chat/jobs`) | Todo número e identificador precisa estar nos fatos; sem dado bancário; cita o alvo e o valor. Reprovado: usa o modelo fixo. |

Tudo o que a IA propõe, e o que foi aceito ou corrigido, fica em `saida_ia_conferencia.csv`. Sem token, ou com o proxy fora do ar, o fluxo segue só com as regras.

### Segurança

Nem dinheiro nem dado saem sem motivo (`seguranca.py`):

- O agente só fala com o fornecedor por um canal cuja última troca foi confirmada.
- Toda mensagem de saída é bloqueada se tiver conta, agência, linha digitável, chave PIX, CPF ou CNPJ de terceiro.
- Logs, pedidos de aprovação e o que vai para a IA levam os números mascarados.
- Quem lê conteúdo de terceiros (o Jev) não tem ferramentas e só devolve rótulos.

## Resultados (fila real de 03/11 a 13/11: 876 títulos, R$ 10,2 milhões)

| Métrica | Valor |
|---|---|
| Títulos que seguem sem nenhuma mão humana | **847 de 876 (96,7%)** |
| Na operação contínua, decisões executadas sem nenhuma pessoa (inclui a varredura de duplicatas em aberto) | **862 de 897 (96,1%)** |
| Dinheiro indevido barrado **com prova documental** (duplicidade, pagamento fora do lote, excesso de valor) | **R$ 396.665** |
| Com evidência forte de golpe (conta trocada sem confirmação, boleto de outro CNPJ) | R$ 206.180 |
| Duplicatas em aberto canceladas antes de entrar em lote (vencem de 16/11 a 14/01) | R$ 162.850 |
| Custo no pior caso (atraso de retidos + todos os acionamentos como desnecessários) | R$ 5.194 |
| Dinheiro indevido, mesmo se nenhum aprovador responder | R$ 0 (operação contínua: 21 pedidos sem resposta, 15 títulos atrasados, R$ 17.966 de atraso) |
| Atraso na operação contínua com respostas normais | 2 títulos, R$ 1.670 (a aprovação dupla do golpe da Embalarte saiu 1h depois do primeiro fechamento) |
| Custo da IA (Jev) para triar 246 e-mails | US$ 0,009 |

Placar pela fórmula do `parametros.md`, só na janela: **sem os agentes, −R$ 602.845; com os agentes, −R$ 5.194 no pior caso.**

### Lançamento assistido (resposta ao cadastro manual)

No histórico de 7.082 títulos digitados:

- 100% teriam o rascunho pronto a partir do XML;
- 29 erros de fator 10 e 89 cópias nasceram na digitação;
- as 837 diferenças de vencimento eram ajuste legítimo para dia útil;
- 260 parcelas de NF já recebidas estão sem título (rascunhos prontos, R$ 2,6 milhões).

O catálogo não tem ferramenta para lançar título, então o agente prepara o rascunho e o analista confirma.

## Como validamos (e onde a IA errou)

- **Testes escritos antes do código:** segurança 18/18 e checagem de saída 18/18. Na primeira rodada, cada conjunto passou em 3/11 e 11/18.
- **Holdout selado por SHA-256:** 11 tipos de caso escritos antes da conferência de 3 vias e rodados uma vez. Perda de R$ 0.
- **Histórico:** 0 falsos alarmes fortes em 4.698 pagamentos legítimos.
- **Teste da conferência da IA** contra um servidor que erra de propósito: pessoa fora da política, número inventado e dado bancário no texto foram todos corrigidos (9/9).
- **Aprovação não respondida:** 7 cenários simulados. A regra de escalar 1h antes do fechamento falhou (R$ 8,3 mil de atraso) e foi trocada por escalar após 2h úteis sem resposta (R$ 1,7 mil).

### Erros encontrados e corrigidos

| Erro | Como foi pego |
|---|---|
| Destino julgava pagamentos antigos com o cadastro de hoje (153 falsos alarmes) | Rodada no histórico |
| Multa e juros legítimos tratados como fraude | Rodada no histórico |
| NF contada duas vezes contra a OC (1.115 falsos alarmes) | Rodada no histórico |
| Reversão de cadastro mandava PIX para uma string de TED | Holdout |
| Conta escrita depois de uma barra escapava da máscara | Teste de segurança |
| Jev não ligava a segunda via falsa vinda de `@outlook` ao fornecedor | Leitura linha a linha da triagem real |
| Roteamento do Jev real: 7 de 10 escolhas com confiança abaixo de 0,7, corrigidas pela regra | `saida_ia_conferencia.csv` |
| Chat do dossiê falhou nas 17 chamadas da 1ª rodada real (o GLM não está liberado para o token); o fluxo seguiu com o modelo fixo | `diag_chat.py`; o sistema agora tenta 4 rotas e usa a que funciona (`latest`). Na rodada final da operação contínua, os 17 pedidos de aprovação foram redigidos pela IA e aprovados na conferência |
| Os pedidos de reagendamento mostravam ao aprovador o valor **antes** da correção (ex.: R$ 48.380 em vez de R$ 4.838) e geravam 2 aprovações desnecessárias (o 3º reagendamento, de R$ 9.087,38, precisa mesmo de aprovação) | Leitura dos dossiês redigidos pela IA: ela repetiu fielmente um fato errado vindo do nosso código |
| Na operação contínua, o título legítimo que voltava ao lote depois de aprovado era cancelado como duplicata da própria reserva | Comparação título a título entre a operação contínua e a execução por lote |

## Limitações (o que não provamos)

- A base é sintética, e as regras foram escritas olhando esses mesmos dados. Os testes foram escritos pelo próprio time.
- A operação contínua anda sobre o retrato de 02/11: a base não traz notas, e-mails e títulos que chegariam durante a semana.
- As aprovações humanas são simuladas, com tempos de resposta estimados por nós (2h, 3h e 4h úteis), e o mensageiro (`mensageiro.html`, o mesmo de `app/index.html`) é uma simulação de canal alimentada pelo log real dos agentes.
- Golpes contra a própria empresa (falso token do banco, falso protesto) ainda não têm rota para um humano.
- Na primeira rodada real, o chat de redação do dossiê falhou em todas as chamadas. O sistema agora tenta 4 rotas de chat; `diag_chat.py` mostra qual funciona com o token.
- A conferência do dossiê garante que a IA é fiel aos fatos, não que os fatos estão certos (foi assim que o erro do valor antes da correção apareceu).
- O roteamento pelo Jev teve confiança baixa na maioria dos casos (2 de 9 aceitos na 2ª rodada real; nos 7 corrigidos, a IA escolheu a mesma pessoa da regra em 5). Mantivemos o limiar de 0,7 para não ajustá-lo olhando o resultado.

## Como rodar

Requisitos: Python 3.11+ e `pandas`. A pasta `dados/` já traz a base fictícia fornecida pelo desafio Pagamentos do GoHacks Talent 2026.

```bash
python rodar.py                 # fluxo completo só com regras
python rodar.py --ia            # com IA (precisa de AI_PROXY_API_KEY no .env)
python operacao.py [--ia]       # operação contínua hora a hora, em 3 cenários de resposta humana
python painel_autonomia.py      # o que os agentes fizeram sozinhos e onde uma pessoa entrou
python placar.py                # placar conservador
python latencia.py              # e se ninguém aprovar a tempo
python lancamento_assistido.py  # medição do cadastro manual
python teste_seguranca.py && python teste_saida_v2.py && python teste_ia_fluxo.py
python holdout_v2.py            # holdout selado (confira antes com sha256sum -c holdout_v2.sha256)
python gerar_mensageiro.py mensageiro.html   # gera a página do app a partir do log (copie para app/index.html)
```

No Windows, use `python -X utf8`. O arquivo `.env` (com `AI_PROXY_API_KEY=...`) fica fora do git.

## Arquivos

| Arquivo | O que é |
|---|---|
| `livro.py` | Livro de Obrigações e conciliação de extratos |
| `destino.py` | Destino confiável (troca de conta, boleto) |
| `tres_vias.py` | Conferência OC × NF × SEFAZ |
| `ambiente.py` | Ferramentas com papéis, alçadas, aprovação assíncrona e log |
| `operacao.py`, `painel_autonomia.py` | Operação contínua e painel de autonomia |
| `agentes.py` | Conciliador e Analista |
| `seguranca.py` | Canal confiável, checagem de saída e máscara |
| `ia.py`, `ia_fluxo.py`, `proxy_client.py` | IA: triagem, roteamento e dossiê, sempre conferidos |
| `placar.py`, `latencia.py`, `lancamento_assistido.py` | Medições |
| `teste_*.py`, `holdout_v2.py` | Validação |
| `gerar_mensageiro.py`, `mensageiro_modelo.html`, `mensageiro.html` | Simulação do canal de mensagens: o modelo recebe os dados do log e gera a página pronta (`mensageiro.html` e `app/index.html`) |
| `app/index.html` | O app publicado pelo GitHub Pages |
| `dados/` | Base fictícia do desafio |
| `demo_submissao.mp4`, `demo_mvp.mp4` | Vídeos de demonstração |
| `Pitch_Pagamentos_Gogroup.pdf`, `.pptx` | Pitch |
