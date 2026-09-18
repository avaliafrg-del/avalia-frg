# Avalia FRG
### Plataforma Municipal de Simulados e Acompanhamento da Aprendizagem
Secretaria Municipal de Educação — Prefeitura de Fazenda Rio Grande

Sistema para professores: cadastra a turma, gera um cartão-resposta nominal por
aluno com QR Code, corrige pela câmera do celular e guarda as notas por escola,
turma e aluno.

- **Interface web** em português, sem jargão técnico
- **Stack:** Python 3.11 · FastAPI · OpenCV (headless) · SQLAlchemy · Pillow · qrcode · pypdf
- **Deploy:** container Docker multi-stage no Google Cloud Run
- **Provas de 1 a 140 questões** numa folha só, com 5 alternativas (A–E)

---

## 1. O que o professor faz

A navegação fica num **menu lateral em cascata**, agrupado pelo que o professor
está fazendo — Cadastro, Avaliação, Resultados e Sistema — em vez de uma fila de
abas no topo. Com sete telas, a fila já não cabia em tela de notebook, e nada na
ordem dizia o que vinha antes do quê. Os grupos recolhem, e no celular o menu
vira uma gaveta.

**Turmas** → cadastra a escola, a turma e cola a lista de alunos da secretaria.

**Provas** → digita o título, marca o gabarito e anexa o documento da prova.
O sistema devolve um PDF com a prova na frente e **um cartão por aluno**, cada
um com o nome já impresso e um QR Code próprio.

**Corrigir** → abre a câmera, aponta para a folha e captura. Ou arrasta as fotos
já tiradas. O gabarito **e o aluno** saem do QR Code: não se digita nada.

**Boletim** → notas da turma, quem ainda não entregou, e o acerto por questão.
Exporta em CSV.

**Alunos** → histórico de cada aluno no ano, com média, evolução e tendência.

**Análise** → o diagnóstico pedagógico: conteúdos mais frágeis por ano escolar,
questões mais erradas com o erro que os alunos cometeram, e orientações prontas
para enviar às escolas.

**Comparar escolas** → duas ou mais escolas lado a lado, com margem de erro em
cada número e a diferença testada estatisticamente.

**Acessos** (só para quem administra) → emite e revoga os tickets dos colegas.

Na primeira vez que abrir, o sistema pede para criar a conta inicial e entrega
um ticket de administrador. Depois disso, todo login pede email, senha e ticket.

### Por que um cartão por aluno

Para guardar a nota no nome certo, o sistema precisa saber de quem é a folha.
O campo "Nome" escrito à mão não é legível por máquina — reconhecer letra
cursiva é um problema bem mais difícil e bem menos confiável do que ler bolhas.

A solução é o QR carregar o identificador do aluno: `P12.A345*ABCDE` significa
prova 12, aluno 345, gabarito ABCDE. O professor entrega a folha certa para cada
aluno e a nota vai sozinha para o boletim. Cartões avulsos (sem turma) continuam
funcionando, mas a correção avisa que a nota não foi guardada em vez de falhar
em silêncio.

---

## 2. Estrutura de arquivos

```
omr-api/
├── main.py                      # HTTP: rotas, CORS, validação de upload
├── dados/                       # Camada de banco, isolada do resto
│   ├── __init__.py              # fachada: `import dados as db`
│   ├── conexao.py               # engine, sessões, startup do schema
│   ├── modelos.py               # as tabelas, e nada além delas
│   ├── migracoes.py             # põe o banco existente em dia, sem apagar
│   ├── erros.py                 # exceções que a camada HTTP traduz
│   └── repositorios/            # consultas e regras, um módulo por assunto
│       ├── comum.py             # busca por id compartilhada
│       ├── escolas.py  turmas.py  alunos.py
│       ├── provas.py            # questões e cartão próprio
│       ├── resultados.py        # notas e estatísticas
│       ├── historico.py         # desempenho ao longo do ano
│       ├── acesso.py            # usuários, senhas e sessões
│       ├── tickets.py           # autorização de entrada
│       ├── configuracao.py      # segredo do QR
│       └── manutencao.py        # backup e limpeza
├── seguranca.py                 # Senhas, sessões e assinatura do QR
├── analitico.py                 # Diagnóstico pedagógico: conteúdo, questão, ano escolar
├── comparativo.py               # Comparação entre escolas, com margem de erro
├── permissoes.py                # Papéis e escopo de dados
├── relatorio.py                 # PDF e CSV do convidado, que não tem banco
├── recuperar_admin.py           # Reemite o ticket de admin quando ele se perde
├── static/
│   ├── index.html               # A interface
│   ├── ajuda.js                 # Conteúdo do Modo explicação
│   ├── logo-avalia.png          # Marca completa — tela de login
│   ├── logo-marca.png           # Marca compacta — canto do menu
│   └── logo-institucional.png   # Prefeitura + Secretaria — rodapé
├── lote.py                      # Extrai folhas de um PDF ou ZIP para correção em massa
├── escolaridade.py              # Escala de anos escolares (1º ano … 3ª série)
├── estampa.py                   # Adiciona âncoras e QR ao cartão do professor
├── deteccao_grade.py            # Descobre onde estão as bolhas num cartão de terceiro
├── omr_engine.py                # Visão computacional (todo o pipeline OMR)
├── gerador.py                   # Desenha o cartão, gera o QR, monta o PDF
├── schemas.py                   # Modelos Pydantic de entrada e saída
├── static/
│   └── index.html               # Interface do professor (HTML/CSS/JS puro)
├── requirements.txt             # Dependências com versões fixadas
├── Dockerfile                   # Build multi-stage para Cloud Run
├── .dockerignore
├── .gcloudignore
├── README.md
└── tests/
    └── test_omr.py              # 246 testes, sem imagens versionadas
```

**Separação de responsabilidades:** `omr_engine.py` e `gerador.py` não conhecem
FastAPI nem HTTP. São Python puro, reutilizáveis em batch, CLI ou Cloud
Functions sem alterar uma linha.

---

## 3. Identidade visual e Modo explicação

### As cores vêm da logo

O azul e o verde do sistema são **exatamente** os da marca, amostrados do
arquivo:

| Cor | Código | Onde entra |
|---|---|---|
| Azul | `#013682` | Barra lateral, títulos, ações principais |
| Verde | `#067821` | Indicadores positivos e item ativo do menu |
| Branco | `#FFFFFF` | Fundo de todo conteúdo |

Usar um azul *próximo* mas diferente deixaria a logo parecendo colada por cima
da tela. Um teste verifica que esses dois códigos continuam no arquivo, e que
as cores das versões anteriores não voltaram.

### A logo fica sempre sobre branco

O azul da marca é o mesmo azul do sistema — sobre a barra lateral escura, ela
**desapareceria**. Por isso o cabeçalho é branco, e é nele que a marca fica; o
azul escuro vai para a lateral e o rodapé.

Três recortes, todos com fundo transparente:

| Arquivo | Onde | Por quê |
|---|---|---|
| `logo-avalia.png` | Centro da tela de login | A marca completa, com o subtítulo |
| `logo-marca.png` | Canto esquerdo do menu | Só "AVALIA FRG": o subtítulo, em 42 px de altura, viraria borrão |
| `logo-institucional.png` | Rodapé, sobre selo branco | Brasão + Prefeitura + Secretaria |

O fundo branco do JPEG original foi tornado transparente. **Nenhuma cor da marca
foi alterada** — só o fundo, e isso é o que permite encaixá-la sobre o selo do
rodapé sem um retângulo claro em volta.

### Os detalhes decorativos

Acabamento, sem função. Se forem removidos, nada se perde além do visual:

- **Faixa azul-verde** no cabeçalho e no topo de cada bloco — é o traço que
  fecha a logo, repetido em escala menor, para cabeçalho e marca lerem como uma
  peça só.
- **Fundo pontilhado** quase invisível: bolhas de cartão-resposta, o motivo do
  produto virando textura.
- **Título e trilha** em cada tela ("Avaliação › Criar prova"), com a escola e
  turma em contexto à direita — evita a dúvida de "esta nota foi para qual
  turma?".
- **Etiqueta do papel** ao lado do nome: quem usa três tipos de conta precisa
  saber de relance com qual está dentro.
- **Rodapé institucional** com o selo da Secretaria.

### Modo explicação

Botão no topo da página. Ligado, cada tela ganha uma caixa com o passo a passo
daquela tela — não um texto genérico: o conteúdo muda conforme o painel aberto.

A aba **Ajuda → Como usar** traz o manual completo, e cada tela tem quatro
seções:

| Seção | O que traz |
|---|---|
| Passo a passo | O caminho, na ordem de fazer |
| O que cada campo significa | Campo por campo, incluindo as pegadinhas |
| O que costuma dar errado | Impressão em 100%, sombra na foto, ano escolar |
| Dúvidas frequentes | As perguntas que aparecem de verdade |

O botão **Imprimir este manual** gera uma versão limpa, sem menu nem botões,
para deixar impressa ao lado do computador da escola.

O texto vive em `static/ajuda.js`, separado da interface, porque vai ser
corrigido com frequência — a cada dúvida que um professor trouxer. Mexer num
arquivo de texto é menos arriscado que mexer no meio do HTML.

Um teste confere que **toda tela tem artigo de ajuda**. Uma tela sem explicação
é pior que nenhuma ajuda: o professor liga o modo e encontra o vazio.

---

## 4. Papéis de acesso

Três papéis, pensados para uso municipal. A regra que governa tudo:
**rota nova nasce fechada**, e um teste varre todas as rotas para garantir isso.

| | Administrador | Escola (membro) | Convidado |
|---|---|---|---|
| Quem usa | Secretaria | A escola | Professor avulso |
| Cadastrar escola, turma, aluno, prova | sim | **não** | não |
| Corrigir provas | sim | **sim** | sim |
| Ver dados | todas as escolas | **só a própria** | nenhum |
| Comparar escolas | sim | **não** | não |
| Criar usuários e tickets | **só ele** | não | não |
| Onde ficam as notas | banco | banco | **em lugar nenhum** |

### Por que a escola não edita cadastro

Quem monta a avaliação é a secretaria; a escola aplica e corrige. Se cada escola
pudesse alterar turma, aluno ou gabarito, a avaliação deixaria de ser comparável
entre elas — que é justamente o propósito de existir uma prova comum.

Corrigir **grava** resultado, e isso é permitido: bloquear a escrita de nota
esvaziaria o papel.

### O primeiro acesso é sempre administrador

A primeira conta criada no sistema nasce **admin** por definição — é ela que cria
todas as outras. A tela mostra o **ticket permanente** e exige confirmação de que
você anotou antes de continuar. O código também vai para o log do servidor, na
linha `TICKET DE ADMINISTRACAO`.

**Se o ticket se perder**, o administrador não fica trancado para fora:

```powershell
python recuperar_admin.py
```

Emite um novo, já vinculado à conta. A proteção é o acesso ao computador: quem
roda isso já está na máquina, com o arquivo do banco na mão — e quem tem isso
já poderia fazer o que quisesse com os dados de qualquer forma.

### Conta de escola sem escola é recusada

Um membro sem vínculo entra no sistema e não vê nada. O cadastro é recusado no
ato, em vez de criar um acesso que parece funcionar e não funciona. Vale a mesma
lógica do resto: falhar fechado, e falhar visível.

### O convidado leva o resultado embora

Sem banco, o trabalho sumiria ao fechar a aba. O navegador acumula as correções
da sessão e o servidor devolve um **PDF** com notas, média e acerto por questão
— ou um **CSV** para somar com outras avaliações. O conteúdo vem do navegador,
então há teto de linhas: sem isso um payload inflado viraria um PDF de mil
páginas.

### A varredura automática

`test_nenhuma_rota_de_dados_responde_ao_convidado` percorre **todas** as rotas
registradas e confirma que nenhuma responde 2xx a um convidado, exceto as de uma
lista explícita. Um endpoint criado daqui a seis meses, sem checagem de papel,
aparece como falha em vez de virar vazamento silencioso — e liberar uma rota
nova fica visível no diff.

Ela já pegou três furos reais durante a construção: `/api/turmas`, `/api/provas`
e uma listagem que devolvia lista vazia em vez de 403. Devolver "vazio" em vez
de "proibido" é pior, porque mascara a falta da regra.

---

## 5. Segurança

Duas proteções distintas, em `seguranca.py`, sem nenhuma dependência externa —
tudo sai de `hashlib`, `hmac` e `secrets`.

### Login com ticket de acesso

Entrar exige **três coisas**: email, senha e um **ticket de acesso**.

A senha diz *quem* é a pessoa; o ticket diz que ela *continua autorizada*. Com
os dois separados, cortar o acesso de um professor que saiu da escola é revogar
um ticket — não trocar a senha de ninguém.

```
PF-A3KM-9XQT-B7WZ
```

12 caracteres num alfabeto de 31, cerca de 59 bits de entropia. O alfabeto
exclui `O`, `0`, `I`, `1` e `L`: o código vai ser lido de um papel e digitado à
mão, e essas confusões viram chamado de suporte. Hífens, espaços e minúsculas
são normalizados — um código certo recusado por causa de um hífen a menos seria
trabalho de suporte para nada.

**O ticket se prende à primeira conta que o usar.** A partir daí não serve para
mais ninguém, então repassar o código a um colega não concede acesso.

**Revogar derruba a sessão aberta na hora.** Deixar a pessoa dentro do sistema
até a sessão vencer seria revogar só no papel.

O banco guarda apenas o **hash** do ticket, como faz com a senha. O código
completo aparece uma única vez, no momento em que é emitido — nem quem
administra consegue recuperá-lo depois. Se perder, revogue e emita outro.

Na primeira execução, a conta inicial vira administradora e recebe um ticket
permanente, mostrado na tela e também registrado no log do servidor, para não se
perder se a janela fechar antes da hora.

### Senhas e sessões

Nome de aluno é dado pessoal sob a LGPD, então o sistema não pode ficar aberto.
Senhas com PBKDF2-SHA256 e 200 mil iterações; sessão em cookie `httponly` com
validade de 12 horas, e o banco guarda só o **hash** do token — se o banco
vazar, o que está lá não serve para entrar.

O limitador de tentativas conta apenas os logins que **falharam**. Contar acerto
junto travaria quem só está usando o sistema: numa escola atrás de um único IP,
alguns professores entrando na mesma manhã esgotariam a cota sem ninguém ter
errado nada.

A proteção é feita por middleware com lista de **exceções**, não de rotas
protegidas. É de propósito: esquecer de proteger um endpoint novo seria um
vazamento silencioso, enquanto esquecer de liberar um endpoint público dá erro
visível na hora.

Na primeira execução não existe nenhum usuário, e a interface oferece criar a
conta inicial. Esse endpoint se fecha sozinho depois — do contrário viraria um
cadastro público no sistema da escola.

### O gabarito saiu do papel

Esta é a mudança que mais muda o comportamento do sistema. Antes, o QR do cartão
continha as respostas em texto puro: qualquer aluno com um celular lia o gabarito
da própria prova antes de entregar.

Agora os cartões gerados a partir de uma turma levam **só a referência**:

```
P12.A345-XRQWBCM5
 │   │       └── assinatura HMAC-SHA256 (8 caracteres em base32)
 │   └────────── aluno 345
 └────────────── prova 12  →  o gabarito vem do banco
```

Dois ganhos de uma vez: o gabarito deixa de estar no papel, e o QR de uma prova
de 140 questões cai de **49 para 25 módulos** — de 4,6 para 9,0 pixels por
módulo impresso, o que torna a leitura muito mais tolerante a foto ruim.

Base32 usa apenas `A-Z` e `2-7`, todos dentro do conjunto alfanumérico do QR.
Uma assinatura em base64 empurraria o código para o modo byte e engordaria
justamente o caso das provas longas.

### O que a assinatura impede

| Tentativa | Resultado |
|---|---|
| Aluno lê o QR do próprio cartão | Vê `P12.A345-XRQWBCM5`. O gabarito não está ali |
| Aluno imprime um cartão com gabarito próprio | Corrige e mostra a nota, mas **não grava** no boletim |
| Aluno troca o `aluno_id` no QR | Assinatura não confere → correção recusada |

Cartões impressos antes desta versão continuam sendo lidos e corrigidos — só não
entram no registro, e a tela explica o motivo. Para voltar ao comportamento
antigo existe `EXIGIR_ASSINATURA=0`, mas isso reabre a fraude.

A chave HMAC vem de `SEGREDO_QR`; sem ela, é gerada uma vez e guardada **no
banco**, não em disco. No Cloud Run o disco é efêmero, e um segredo perdido
invalidaria a assinatura de todos os cartões já impressos.

---

## 6. Diagnóstico pedagógico

A correção é meio; o diagnóstico é o fim. Esta parte responde à pergunta que o
professor realmente tem: **o que precisa ser retomado.**

### Sem conteúdo cadastrado, não existe diagnóstico

"Erraram a questão 15" só vale dentro daquela prova. Para virar orientação
precisa ser "erraram frações" — que atravessa provas, turmas e anos.

Por isso cada questão tem um campo **conteúdo**, preenchido ao lado da resposta
na hora de montar o gabarito:

```
01  (A) (B) (C) (D) (E)   [ Frações        ]
02  (A) (B) (C) (D) (E)   [ Frações        ]
03  (A) (B) (C) (D) (E)   [ Geometria      ]
```

Fica ali, e não numa tela separada, porque classificado na hora é preenchido;
deixado para depois, quase nunca é. As questões sem conteúdo ficam de fora da
análise, e a tela informa **quantas** — silenciar isso esconderia do professor
que o diagnóstico está incompleto.

O campo sugere conteúdos já usados na escola. Sem isso, "Frações", "fracao" e
"FRAÇÕES" virariam três conteúdos distintos e fragmentariam a amostra.

### Ano escolar é dado estruturado, não texto livre

Comparar "o 2º ano" entre escolas exige que todas escrevam igual. A turma
continua com apelido livre ("8º B", "Turma da Manhã"); o **ano escolar** é uma
lista fechada (`escolaridade.py`) com ordem própria — ordenação alfabética
poria "10º" antes de "2º".

O cadastro aceita `2`, `2º ano` ou `EF2` e normaliza para o mesmo código.

### Amostra pequena não vira conclusão

Uma questão respondida por 8 alunos com 40% de acerto não distingue "conteúdo
mal aprendido" de acaso. Todo número carrega o tamanho da amostra, e abaixo de
**25 respostas** o resultado aparece marcado como *amostra pequena* e **não
gera orientação**.

Um sistema que produz recomendações confiantes a partir de ruído é pior que um
que se cala: a escola descobre na primeira conferência e para de confiar em
tudo o mais.

### O erro que os alunos cometeram

Quando metade da turma escolhe a **mesma** alternativa errada, não houve chute —
há um equívoco conceitual específico, e dá para saber qual. Esse é o dado mais
acionável do painel.

Distinguir isso de acaso exige três condições simultâneas:

| Condição | Por quê |
|---|---|
| ≥ 50% dos **erros** na mesma alternativa | Com 5 alternativas, o chute espalha ~25% em cada errada |
| ≥ 25% da turma inteira | 100% dos erros não diz nada se só dois alunos erraram |
| ≥ 10 respostas | Abaixo disso qualquer proporção é ruído |

Só a fatia da turma não bastava: em teste com 20 alunos e erro aleatório, o
acaso concentrou 35% numa alternativa e teria sido anunciado como "erro
conceitual".

### O que a escola recebe

```
[PRIORIDADE] 2º ano de EM Vila Nova: Frações teve 37.5% de acerto em 4
questões, com 120 respostas. Recomenda-se retomada prioritária do conteúdo.
Na questão 2 da prova "Diagnóstica Matemática" (resposta certa: E), 53.3% dos
15 alunos marcaram A — a concentração num mesmo erro sugere equívoco
conceitual, e não chute.
```

Cada frase traz a evidência junto com o veredito: percentual, tamanho da
amostra e a questão de origem. A coordenação consegue conferir de onde saiu o
número, em vez de receber uma recomendação sem lastro.

**Faixas usadas:** abaixo de 40% é `crítico`, abaixo de 60% é `atenção`, acima
é `adequado`. Estão em `analitico.py` e são o primeiro lugar a ajustar se a
rede tiver outro parâmetro.

**Em branco não conta como erro.** Misturar as duas coisas inflaria a
dificuldade aparente do conteúdo — não saber e não ter chegado na questão são
situações diferentes.

---

## 7. Comparar escolas

A comparação entre escolas é a informação mais delicada do sistema. Ela
influencia decisão de formação, alocação de material e, na prática, a reputação
de equipes inteiras. Um ranking apresentado sem cuidado transforma ruído em
veredito.

### Margem de erro em todo número

Uma escola com 18 alunos e 71% de acerto **não** está acima de outra com 120
alunos e 68% — a primeira tem margem de ±10 pontos.

| Respostas | Margem de erro (95%) |
|---|---|
| 10 | ±28 pontos |
| 50 | ±13 pontos |
| 100 | ±9 pontos |
| 300 | ±5 pontos |
| 1000 | ±3 pontos |

### A diferença é testada, não apenas calculada

Todo par de escolas passa por um teste z para duas proporções. Resultados:

| Comparação | Diferença | Veredito |
|---|---|---|
| 70% × 60%, 20 alunos cada | 10 pontos | Não passa no teste |
| 70% × 60%, 100 alunos cada | 10 pontos | Não passa no teste |
| 70% × 60%, 1000 cada | 10 pontos | Diferença real |
| 62% × 58%, 100 cada | 4 pontos | Não passa no teste |

Quando não passa, a frase diz isso com todas as letras: *"estão empatadas
dentro da margem de erro; com estas amostras, não dá para afirmar que uma vai
melhor que a outra."*

### O que a tela não faz

Não produz ranking de qualidade. Perfil de entrada dos alunos, contexto do
bairro e rotatividade de professores não estão nestes dados — e concluir sobre
eles a partir daqui seria atribuir à escola um resultado que ela não controla
sozinha. A tela mostra **onde há diferença de desempenho**, não qual escola "é
melhor", e traz esse aviso em rodapé fixo.

Só entram na tabela conteúdos classificados em **todas** as escolas escolhidas:
uma linha com buraco convida a comparar o que não é comparável.

---

## 8. Correção em massa

Um arquivo só, com a turma inteira dentro. É como o material chega na prática:
o scanner do administrativo devolve um PDF de 40 páginas, ou alguém junta as
fotos do celular num ZIP.

| Formato | Como é lido |
|---|---|
| **PDF** | Uma folha por página, rasterizada a 200 dpi sob demanda |
| **ZIP** | Uma folha por imagem, em ordem de nome |
| **Imagem** | Tratada como lote de uma folha |

O sistema conta as folhas **antes** de começar e informa o tempo estimado — sem
isso o professor fica olhando uma tela parada sem saber se travou. Uma folha
ilegível não derruba o lote: volta identificada pela origem (*"página 7"*) para
o professor achar o papel físico.

Lixo que o macOS coloca em todo ZIP (`__MACOSX/`) e arquivos que não são imagem
são ignorados, em vez de virarem "folhas ilegíveis".

Medido em lote de 12 folhas: **cerca de 0,6 s por folha**, com as notas indo
direto para o boletim no nome de cada aluno.

**Limites:** 120 folhas e 120 MB por arquivo. Acima disso, divida o PDF — e
lembre que o Cloud Run tem timeout de requisição; um lote de 100 folhas leva
cerca de um minuto.

---

## 9. Banco de dados

### Por que virou um pacote

O `database.py` tinha mil linhas com quatro assuntos misturados: definição de
tabelas, consultas, autenticação e estatísticas. Achar onde uma regra morava
exigia rolar o arquivo inteiro, e qualquer mudança tocava um arquivo que todo o
resto importava.

Agora são camadas com responsabilidade única:

| Módulo | Sabe sobre |
|---|---|
| `conexao.py` | **Como** o banco é alcançado — o único que conhece `DATABASE_URL` |
| `modelos.py` | A **forma** dos dados. Nenhuma consulta mora aqui |
| `erros.py` | As exceções que a API traduz em 404, 401 e 403 |
| `repositorios/` | As **operações**, um módulo por assunto |

Separar forma de operação tem um efeito prático: o schema inteiro cabe numa
tela, e nenhuma regra de negócio se esconde no meio da definição de uma coluna.

### Atualizar não apaga mais o banco

Até a versão 7, toda atualização vinha com a instrução *"apague o
provafacil.db"*. Isso funciona enquanto o sistema está sendo montado e deixa de
funcionar no minuto em que há nota de aluno gravada.

No startup, `migracoes.sincronizar()` faz três coisas nesta ordem:

1. Cria as **tabelas** que faltam
2. Acrescenta as **colunas** que faltam, comparando o banco real com os modelos
3. Aplica as **migrações manuais** pendentes — para o que o passo 2 não resolve

O passo 2 cobre o caso comum de evolução de schema, que é acrescentar campo, e
dispensa escrever migração para cada um. O passo 3 existe porque nem toda
mudança é aditiva: renomear coluna, converter tipo ou preencher campo novo a
partir do antigo precisam de instrução explícita. Fingir que tudo é aditivo
trocaria perda de dados por dado silenciosamente errado.

Testado com banco populado: duas colunas removidas e uma tabela apagada, e
depois da sincronização as escolas, alunos e notas continuavam lá.

**Dois limites conscientes.** Coluna nova precisa ser opcional ou ter padrão —
não dá para acrescentar campo obrigatório numa tabela que já tem linhas sem
saber o que pôr nelas; nesse caso o sistema registra erro no log em vez de
inventar um valor. E **remover** coluna não é automático: some do modelo e
continua no banco, porque apagar dado precisa ser decisão escrita.

A versão do schema aparece em `/api/health`, útil quando alguém sobe o código
novo sobre um banco antigo.

### Backup

`GET /api/admin/backup` baixa escolas, turmas, alunos, provas e notas em JSON.

Copiar o arquivo `provafacil.db` só funciona no SQLite, e em produção o banco é
Postgres — um backup que só serve em desenvolvimento não é backup.

Senhas e tickets **não** entram no arquivo: backup circula por e-mail e pen
drive, e credencial não pode viajar assim.

A tela pergunta se o backup foi feito antes de deixar apagar os dados.

### A fachada

O resto do sistema importa de um lugar só:

```python
import dados as db
db.criar_escola(sessao, "EM Vila Nova")
```

Quem chama não precisa saber que `criar_prova` mora em `repositorios/provas.py` e
`autenticar` em `repositorios/acesso.py`. Mover uma função entre repositórios
passa a ser detalhe interno, sem tocar em `main.py` nem nos testes.

```
Escola                          Usuario
  └── Turma (ano_escolar)         ├── Sessao
        ├── Aluno                 └── Ticket
        └── Prova
              ├── Questao (número, correta, conteúdo, habilidade)
              └── Resultado (nota + detalhe questão a questão)
```

`Questao` é a fonte da verdade do gabarito **e** o que carrega o conteúdo
cobrado. Sem ela o sistema corrige provas mas não diagnostica nada.

O `Resultado` guarda o detalhamento completo em JSON, não só a nota. É isso que
permite refazer a análise depois — o acerto por questão da turma, ou conferir um
recurso de aluno — sem precisar da foto original.

Há uma restrição `UNIQUE(prova_id, aluno_id)`: recorrigir a folha de um aluno
**substitui** o resultado anterior. Um professor que refaz uma foto ruim não
deve acabar com duas notas para a mesma prova.

### Escolha do banco

A conexão vem de `DATABASE_URL`. O padrão é SQLite num arquivo local, que
resolve o desenvolvimento e o uso numa escola só.

> **Atenção no Cloud Run:** o disco da instância é efêmero e some a cada reinício
> ou escala para zero. **SQLite ali perde os dados.** Em produção aponte para um
> Postgres (Cloud SQL):
> ```
> DATABASE_URL=postgresql+psycopg://usuario:senha@host:5432/provafacil
> ```
> Nenhuma outra linha do código muda — o SQLAlchemy cuida da diferença. Lembre
> de descomentar `psycopg` no `requirements.txt`.

---

## 10. Usando o cartão-resposta da própria escola

Na aba Provas dá para escolher entre o cartão gerado pelo sistema e **o modelo
da escola**. No segundo caso, o layout original é preservado: o sistema não
redesenha nada, apenas acrescenta duas marcas nas margens.

### Por que só o QR Code não bastaria

O pedido natural é "só coloque o QR Code no meu cartão". Mas o QR identifica a
prova e o aluno — ele não diz onde estão as bolhas nem permite endireitar uma
foto torta. Um cartão com QR e mais nada sairia bonito e **ilegível** para o
sistema.

Faltam duas coisas, e a estampa resolve as duas:

| Marca | Para quê |
|---|---|
| 4 quadrados pretos nos cantos | Retificar a perspectiva da foto |
| QR Code | Identificar prova e aluno |

E falta uma terceira, que não é marca: **saber onde ficam as bolhas.** Cada
escola usa espaçamento, tamanho e número de colunas diferentes.

### O sistema aprende o layout

Quando o cartão é enviado, o `deteccao_grade.py` procura as bolhas na folha já
retificada: filtra contornos por circularidade, agrupa em linhas e colunas, e
separa os blocos de questões pelo tamanho do vão entre eles.

O layout detectado é guardado com a prova e usado depois na correção, no lugar
da geometria calculada. O mesmo motor lê os dois tipos de cartão.

Três filtros foram necessários e cada um tem uma razão específica:

| Filtro | Problema que resolve |
|---|---|
| Zona dos cantos ignorada | Um quadrado tem circularidade 0,785 — as âncoras seriam lidas como bolhas |
| Grupos esparsos descartados | Um "0" impresso no número da questão passa nos testes de forma, mas fica sozinho na coluna |
| Blocos irregulares removidos | Traços e molduras da folha formam colunas soltas |

### A prévia não é enfeite

Depois de analisar, a prévia mostra **retângulos vermelhos sobre cada bolha** —
exatamente onde o sistema vai ler. Detecção automática erra; conferir custa dez
segundos, descobrir o erro depois custa trinta cartões corrigidos errado.

### O desenho do professor não é alterado

Há teste que exige o conteúdo original **idêntico pixel a pixel** fora da área
do QR. Se as margens não tiverem espaço livre, a folha ganha uma borda branca em
vez de receber carimbo por cima do conteúdo — e a tela avisa que isso aconteceu.

### Limites

Funciona em cartões de grade regular, que é o formato de praticamente todo
cartão-resposta. Layouts com bolhas em diagonal, tamanhos misturados ou
alternativas em quadrados vão falhar — por isso a conferência na prévia.

---

### Dois erros que a detecção cometia

**O número da questão virava alternativa.** O dígito `0` de "01" é redondo o
bastante para passar num filtro de circularidade. Cada bloco ganhava uma coluna
fantasma à esquerda, e um cartão de 2 colunas era lido como 14 colunas — recusado
como grade irregular.

A correção usa uma propriedade que toda grade impressa tem: **as bolhas são
todas do mesmo tamanho**. Círculos fora do diâmetro dominante (±28%) são
descartados. O dígito sai; a bolha fica.

**O QR cobria o cartão.** A verificação de espaço livre tolerava 0,5% de pixels
escuros na região. Bastou uma sobreposição de 4 px para cortar a borda de uma
bolha e derrubar a questão inteira. Agora a tolerância é 0,1%, e quando nenhum
canto está livre a folha **ganha uma faixa branca no topo** em vez de receber
carimbo por cima — o conteúdo desce inteiro, sem nada apagado.

---

## 11. Como o cartão e a leitura nunca saem de sincronia

Este é o ponto de arquitetura que mais importa no projeto.

`OMRConfig.calcular_blocos()` é a **fonte única da geometria**: define onde cada
bolha fica no espaço retificado de 800×1000. O `gerador.py` usa essa função para
*desenhar* o cartão; o `omr_engine.py` usa a mesma função para saber onde
*procurar* as bolhas. A única diferença entre os dois é a escala de impressão.

Consequência prática: **não existe calibração manual.** Mudar o número de
questões, a área da grade ou o espaçamento ajusta impressão e leitura ao mesmo
tempo, porque não há geometria duplicada em lugar nenhum.

### O número de colunas não é fixo

A grade testa todas as divisões possíveis (1 a 4 colunas) e fica com a que deixa
a **bolha maior**. Não é o menor número de colunas: numa prova de 30 questões,
duas colunas dobram o tamanho da bolha em relação a uma. A folha vai de 21 mm
por bolha em provas curtas até 3,5 mm nas mais longas.

### O limite vem da geometria, não de um número escolhido

Não existe teto arbitrário no código. `maximo_questoes` é descoberto por busca
sobre a própria geometria: o sistema aumenta o número de questões até a bolha
ficar menor que `passo_minimo` (20 px no espaço retificado, ~3,5 mm impressos —
o mesmo porte das provas de larga escala). Hoje isso dá **140 questões por
folha**. Se você mexer nas margens ou permitir uma quinta coluna, o limite
acompanha sozinho, sem editar constante nenhuma.

| Questões | Layout | Bolha impressa |
|---|---|---|
| 10 | 1 coluna × 10 | ~11,7 mm |
| 30 | 2 colunas × 15 | ~7,8 mm |
| 60 | 3 colunas × 20 | ~5,8 mm |
| 100 | 4 colunas × 25 | ~3,5 mm |
| 140 | 4 colunas × 35 | ~3,5 mm |

---

## 12. Pipeline do algoritmo

| # | Etapa | Implementação |
|---|-------|---------------|
| 1 | Ler o QR | `cv2.QRCodeDetector` na foto, com 3 tentativas (original, ampliada 2×, equalizada) |
| 1b | Formato do QR | `PROVA*ABCDE` — colado, para caber no modo alfanumérico |
| 2 | Bytes → matriz BGR | `cv2.imdecode` + downscale defensivo para 1600 px |
| 3 | Pré-processamento | Normaliza iluminação → `GaussianBlur` → `THRESH_BINARY_INV + THRESH_OTSU` |
| 4 | Localizar âncoras | `cv2.findContours` + filtros de área, 4 vértices, aspecto e solidez |
| 5 | Retificar | `ordenar_pontos()` → `getPerspectiveTransform` → `warpPerspective` (800×1000) |
| 6 | Conferir orientação | Posição do QR na folha retificada; gira 180° se necessário |
| 7 | Corrigir iluminação | Fechamento morfológico estima o fundo; a divisão achata a sombra |
| 8 | Mapear a grade | ROIs pré-calculadas no `__init__`, custo zero por requisição |
| 9 | Detectar marcação | Escurecimento médio por ROI, comparado **entre as 5 alternativas** |
| 10 | Validar | Nada acima do limiar → `em_branco`; duas similares → `rasura` |
| 11 | Corrigir | Compara com o gabarito, calcula a nota na escala 0–10 |

### Por que a iluminação é normalizada antes do Otsu

Não é enfeite. O Otsu global supõe duas populações de brilho bem separadas. Numa
folha com muito espaço em branco e sombra de um lado — o caso de qualquer cartão
fotografado — ele acaba partindo o **próprio gradiente do papel** ao meio.

Medido num cartão de escola com bastante área livre: a máscara marcava **42% da
imagem** como escuro, as âncoras saíam completamente erradas e a correção
devolvia nota 0 sem nenhum aviso. Dividir pela versão borrada remove o gradiente
antes de decidir o corte, e a máscara caiu para **1,3%**.

O fundo é estimado numa versão reduzida da imagem: um desfoque com sigma grande
na imagem inteira levava a suíte de 18 s para 56 s e não acrescentava nada, já
que o que se quer capturar é justamente a variação lenta.

### Por que a etapa 6 existe

As quatro âncoras são simétricas. Uma foto de cabeça para baixo produz um warp
perfeitamente válido, a leitura sai trocada e **o aluno leva zero sem nenhum
aviso**. O QR Code quebra essa simetria: ele é impresso sempre no canto superior
direito, então se aparecer na metade inferior esquerda da folha retificada, a
imagem está invertida e é girada. Há teste de regressão cobrindo isso.

### O tipo do arquivo é decidido pelo conteúdo

Navegador e sistema operacional erram o `content-type` com frequência: um PDF
chega como `application/octet-stream` em várias combinações de Windows e
arrastar-e-soltar. Confiar nesse campo gerava um 415 que o professor não tinha
como entender — o arquivo era um PDF perfeitamente válido.

O `identificar_arquivo()` olha os primeiros bytes (`%PDF-`, `\x89PNG`,
`\xff\xd8\xff`…) e ignora extensão e content-type. PDFs com apenas senha de
proprietário, comuns em sistema de secretaria, são abertos com senha vazia antes
de desistir.

### Por que o QR é gravado sem vírgulas

O QR tem um modo **alfanumérico** (dígitos, A–Z e alguns símbolos) que gasta 5,5
bits por caractere, contra 8 do modo byte. A vírgula e o `|` não pertencem a esse
conjunto e, sozinhos, empurram o código inteiro para o modo byte.

Medido numa prova de 140 questões:

| Formato | Caracteres | Módulos | Pixels por módulo |
|---|---|---|---|
| `P140\|1A,2B,3C…` | 284 | 69 | 3,3 — **não lê** |
| `P140*ABCDE…` | 145 | 45 | 5,0 — lê |

Com o formato antigo, provas de 80 questões para cima já falhavam na foto. O
parser continua aceitando `|`, vírgulas e a forma numerada, então cartões já
impressos seguem funcionando.

### A decisão é relativa, não por limiar fixo

A bolha marcada é a que **destoa das outras quatro da mesma questão** — não a
que passa de um valor combinado. Trocar um critério pelo outro corrigiu duas
falhas que apareciam com folha impressa de verdade:

| Situação | Com limiar fixo | Com comparação relativa |
|---|---|---|
| Sombra do fotógrafo sobre metade da folha | Aquele lado inteiro "parecia preenchido" → **tudo virava rasura** | Sombra escurece as 5 igualmente; a diferença sobrevive |
| Aluno que risca a bolha em vez de pintar | Ficava abaixo do corte → **prova inteira em branco** | O risco continua muito mais escuro que as 4 vizinhas |
| Caneta azul, lápis, caneta preta | Um corte calibrado para uma cor descarta as outras | A cor afeta as 5 juntas e sai da conta |

Antes disso, a imagem passa por uma **correção de iluminação**: um fechamento
morfológico com kernel maior que a bolha apaga as marcas e deixa só o gradiente
de luz; dividir a original por esse fundo devolve a folha como se estivesse sob
luz uniforme.

**Os números não foram escolhidos a dedo.** Em 870 questões de folhas *em
branco* fotografadas com perspectiva, desfoque e sombra, o destaque máximo
medido foi **0,008**. O preenchimento mais fraco que um aluno produz — risco de
lápis claro — mede **0,058**. A margem exigida ficou em **0,05**: seis vezes o
ruído observado.

Um piso absoluto de 0,09 complementa a regra, para o caso da folha limpa: sem
ele, alguma bolha sempre seria "a menos clara" e viraria resposta.

### Fallback em cascata do alinhamento

O campo `alinhamento` na resposta informa qual estratégia foi usada:

1. `"ancoras"` — as 4 marcas de canto foram encontradas *(caminho ideal)*
2. `"contorno_pagina"` — âncoras falharam; usou o maior quadrilátero da cena
3. `"fallback_resize"` — nada foi encontrado; apenas redimensionou

A interface mostra um aviso de "confira este resultado" sempre que o método não
for `ancoras`. Se isso aparecer com frequência em produção, o problema está na
**impressão** ou na **iluminação da foto**, não no código.

---

## 13. Passo a passo — rodar na sua máquina

### 13.1 — Pré-requisitos

- Python 3.10 ou superior (o container usa 3.11)
- Docker apenas a partir do passo 7; `gcloud` apenas no passo 8

### 13.2 — Ambiente virtual e dependências

```bash
cd omr-api
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Se der erro de compilação do numpy, seu Python provavelmente é 3.13+. Use 3.11
ou 3.12.

### 13.3 — Subir o sistema

```bash
python main.py
```

O terminal vai imprimir `Uvicorn running on http://0.0.0.0:8080`. **Deixe esse
terminal aberto** e abra no navegador:

```
http://localhost:8080
```

A interface do professor abre direto. A documentação técnica da API continua em
`/docs`, mas o professor nunca precisa vê-la.

> Para desenvolvimento com recarga automática: `uvicorn main:app --reload --port 8080`

### 13.4 — Testar o ciclo completo sem impressora

1. Na primeira vez, crie a conta inicial e **anote o ticket** que aparece
2. Aba **Turmas**: crie uma escola, uma turma e cole 3 nomes
3. Aba **Provas**: marque o gabarito e clique em **Criar prova e gerar cartões**
4. Abra a prévia, salve a imagem e pinte algumas bolhas de preto num editor
5. Aba **Corrigir**: arraste a imagem — a nota vai aparecer no nome do aluno
6. Aba **Boletim**: veja a nota gravada e quem ainda falta
7. Aba **Alunos**: clique num nome para ver o histórico

### 13.5 — Rodar os testes

```bash
pip install pytest httpx
pytest -v
```

216 testes. Eles geram cartões em memória, **degradam as imagens de propósito**
(perspectiva, desfoque, sombra lateral, compressão JPEG a 80%) e conferem se as
notas continuam corretas.

---

### 13.6 — A câmera

A captura pela câmera usa `getUserMedia` e envia o quadro capturado para o
servidor, que já sabe ler o QR. **Não há biblioteca de leitura de QR no
navegador** — o `BarcodeDetector`, quando existe, só acende o aviso "QR Code
encontrado" para orientar o enquadramento. Quem lê de verdade é o OpenCV no
servidor, então a correção funciona igual nos navegadores que não têm essa API.

Dois pontos práticos:

- **A câmera exige HTTPS** fora de `localhost`. No celular, acessando o Cloud Run
  pelo endereço `https://`, funciona; num IP local `http://`, o navegador bloqueia.
  Por isso o envio de arquivos continua disponível como alternativa.
- Peça `1920×1080` ideal. Resolução baixa demais faz o QR perder módulos e a
  leitura falhar antes de chegar nas bolhas.

---

## 14. Impressão — o que dá errado na prática

| Regra | Motivo |
|---|---|
| Imprima em **tamanho real (100%)**, nunca em "ajustar à página" | Escalas diferentes deslocam as âncoras em relação à grade |
| Papel branco comum, impressão em preto | Papel colorido reduz o contraste do Otsu |
| Não dobre nem escreva sobre os quadrados dos cantos | São eles que permitem endireitar a foto |
| Peça caneta azul ou preta, não lápis | Grafite tem contraste baixo demais |
| Oriente a preencher a bolha por inteiro | Um "X" ou risco fica abaixo do limiar de 0,40 |

Na hora da foto: folha inteira no quadro, com os quatro quadrados visíveis, luz
vindo de cima e sem sombra do fotógrafo sobre o papel. Não precisa estar reta —
o sistema endireita sozinho.

---

## 16. Build e teste do container

```bash
docker build -t prova-facil:local .
docker run --rm -p 8080:8080 -e PORT=8080 prova-facil:local
```

Rode isso **antes** do deploy. Quase todo erro de "Container failed to start" no
Cloud Run aparece aqui primeiro e custa 30 segundos em vez de 5 minutos.

**Sobre o Dockerfile:** o stage `builder` instala as dependências em `/install`
com `build-essential`; o `runtime` copia só o resultado. `opencv-python-headless`
dispensa `libGL`/GTK — apenas `libglib2.0-0` é necessário. `fonts-dejavu-core`
entra porque o Pillow precisa de uma fonte TTF para escrever o texto do cartão;
sem ela o PIL cai numa fonte bitmap minúscula e o cartão sai ilegível.

---

## 16. Deploy no Google Cloud Run

```bash
# 16.1 — configurar (uma vez por projeto)
gcloud auth login
gcloud config set project SEU_PROJECT_ID
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com

# 16.2 — deploy direto do código-fonte
gcloud run deploy prova-facil \
  --source . \
  --region southamerica-east1 \
  --allow-unauthenticated \
  --memory 1Gi \
  --cpu 1 \
  --concurrency 10 \
  --timeout 120 \
  --max-instances 10 \
  --set-env-vars "LOG_LEVEL=INFO"

# 16.3 — abrir
gcloud run services describe prova-facil --region southamerica-east1 --format 'value(status.url)'
```

### Notas de produção

| Assunto | Recomendação |
|---|---|
| **Porta** | Não fixe 8080 no código. O Cloud Run injeta `PORT` e o `CMD` já expande `${PORT}` |
| **Memória** | 1 GiB. Com 512 MiB há risco de OOM em lotes grandes |
| **Timeout** | 120 s: uma turma de 40 fotos leva tempo numa requisição só |
| **Concorrência** | OpenCV é CPU-bound; `--concurrency 10` com 1 vCPU. Suba a CPU antes da concorrência |
| **Threads** | `OPENCV_NUM_THREADS=1` já está no Dockerfile |
| **Cold start** | ~3 s (import do OpenCV). Use `--min-instances 1` se a espera incomodar |
| **CORS** | A interface é servida pelo mesmo domínio, então `CORS_ORIGINS` só importa se você fizer outro frontend |
| **Autenticação** | Remova `--allow-unauthenticated` se não puder ser público |

### Variáveis de ambiente

| Variável | Padrão | Descrição |
|---|---|---|
| `PORT` | `8080` | Porta HTTP (injetada pelo Cloud Run) |
| `DATABASE_URL` | `sqlite:///./avaliafrg.db` | Conexão do banco. **Troque por Postgres no Cloud Run** |
| `SEGREDO_QR` | gerado e guardado no banco | Chave HMAC dos cartões. Trocar invalida os já impressos |
| `EXIGIR_ASSINATURA` | `1` | Se `0`, aceita cartão sem assinatura no boletim (reabre a fraude) |
| `CORS_ORIGINS` | `*` | Origens permitidas, separadas por vírgula |
| `TAMANHO_MAXIMO_MB` | `15` | Limite por arquivo |
| `MAXIMO_ARQUIVOS_LOTE` | `60` | Fotos por requisição de lote |
| `LOG_LEVEL` | `INFO` | Nível de log |

---

## 17. API

A interface consome estes endpoints. Você também pode chamá-los direto.

### Acesso

| Método | Rota | Uso |
|---|---|---|
| `GET` | `/api/auth/estado` | Diz se há sessão e se falta criar a conta inicial |
| `GET` | `/api/admin/backup` | Backup dos dados pedagógicos em JSON (só administrador) |
| `POST` | `/api/admin/limpar-dados` | Apaga os dados pedagógicos (só administrador) |
| `POST` | `/api/auth/primeira-conta` | Cria a conta inicial (só enquanto não houver nenhuma) |
| `POST` | `/api/auth/entrar` | Login com email + senha + **ticket**; devolve o cookie |
| `POST` | `/api/auth/sair` | Encerra a sessão |
| `POST` | `/api/usuarios` | Cria conta (**só admin**). Campos: `papel`, `escola_id` |
| `GET` | `/api/papeis` | Lista os papéis para o seletor |
| `POST` | `/api/relatorio` | PDF ou CSV das correções da sessão |
| `GET` / `POST` | `/api/tickets` | Lista e emite tickets (só administrador) |
| `POST` | `/api/tickets/{id}/revogar` | Corta o acesso e derruba a sessão |

Todas as demais rotas `/api/` respondem **401** sem sessão.

### Análise pedagógica

| Método | Rota | Uso |
|---|---|---|
| `GET` | `/api/escolas/{id}/analise` | Painel completo. Filtros: `ano_escolar`, `disciplina`, `dias` |
| `GET` | `/api/escolas/{id}/analise.csv` | Mesma análise em planilha |
| `GET` | `/api/anos-escolares` | Lista fechada para o seletor |
| `GET` / `PUT` | `/api/provas/{id}/questoes` | Lê e classifica o conteúdo das questões |
| `GET` | `/api/conteudos` | Conteúdos já usados, para sugerir na digitação |

### Histórico

| Método | Rota | Uso |
|---|---|---|
| `GET` | `/api/alunos/{id}/historico` | Notas do aluno no ano, média e tendência |
| `GET` | `/api/turmas/{id}/desempenho` | Grade de notas: alunos nas linhas, provas nas colunas |

### Cadastro

| Método | Rota | Uso |
|---|---|---|
| `GET` / `POST` | `/api/escolas` | Lista e cria escolas |
| `GET` / `POST` | `/api/turmas` | Lista (`?escola_id=`) e cria turmas |
| `GET` / `POST` | `/api/turmas/{id}/alunos` | Lista e adiciona alunos (um nome por linha) |
| `DELETE` | `/api/alunos/{id}` | Remove um aluno |

### Cartão do professor

| Método | Rota | Uso |
|---|---|---|
| `POST` | `/api/provas/{id}/cartao-proprio` | Envia o modelo da escola; estampa e detecta a grade |
| `GET` | `/api/provas/{id}/cartao-proprio/previa` | PNG com a grade detectada marcada |

### Provas

| Método | Rota | Uso |
|---|---|---|
| `GET` / `POST` | `/api/provas` | Lista (`?turma_id=`) e cria provas com gabarito |
| `POST` | `/api/provas/{id}/cartoes` | PDF com um cartão nominal por aluno |
| `POST` | `/api/provas/{id}/previa` | PNG do cartão do primeiro aluno |
| `GET` | `/api/provas/{id}/boletim` | Notas da turma e acerto por questão |
| `POST` | `/api/cartao` | Cartão avulso, sem vínculo com turma |

### Correção em massa

| Método | Rota | Uso |
|---|---|---|
| `POST` | `/api/corrigir-arquivo` | PDF ou ZIP com a turma inteira |
| `POST` | `/api/contar-folhas` | Quantas folhas há, sem corrigir |

### Comparação

| Método | Rota | Uso |
|---|---|---|
| `GET` | `/api/comparativo?escolas=1,2,5` | Filtros: `ano_escolar`, `disciplina`, `dias` |

### Correção

`POST /api/corrigir` — `file` (imagem) + `qr_code_str` **opcional**. Sem o campo,
o gabarito e o aluno saem do QR da própria foto.

```json
{
  "sucesso": true,
  "prova_id": "P12",
  "aluno_id": 345,
  "aluno_nome": "Mariana Alves",
  "nota": 8.0,
  "total_questoes": 10,
  "acertos": 8,
  "erros": 1,
  "em_branco": 1,
  "rasuras": 0,
  "alinhamento": "ancoras",
  "origem_gabarito": "imagem",
  "salvo": true,
  "motivo_nao_salvo": null,
  "detalhamento": [
    {"questao": 1, "marcada": "A", "correta": "A", "status": "correto", "confianca": 0.677}
  ]
}
```

**Valores de `status`:** `correto` · `incorreto` · `em_branco` · `rasura`

`salvo` diz se a nota entrou no banco. Quando é `false`, `motivo_nao_salvo`
explica por quê — cartão avulso, aluno removido, prova apagada — em vez de a
correção falhar em silêncio.

`POST /api/corrigir-lote` — `files` (várias imagens). Uma foto ilegível não
derruba o lote: ela volta com a mensagem de erro e as demais são processadas.

### Outros

| Método | Rota | Uso |
|---|---|---|
| `GET` | `/` | Interface do professor |
| `GET` | `/api/health` | Health check + máximo de questões |
| `GET` | `/docs` | Documentação técnica |
| `POST` | `/api/ler-gabarito` | Extrai o gabarito do QR sem corrigir |
| `POST` | `/api/debug/preview` | PNG da folha retificada com a grade desenhada |

**Erros** — sempre `{"sucesso": false, "erro": "...", "detalhe": null}`, com
`400`, `404`, `413`, `415` ou `500`.

---

## 18. Solução de problemas

| Sintoma | Causa provável | Correção |
|---|---|---|
| "Não foi possível ler o QR Code" | Foto cortando o código, tremida ou escura | Reenquadre com a folha inteira; ou envie `qr_code_str` manualmente |
| `alinhamento: fallback_resize` | Âncoras não detectadas | Confira impressão em 100%, luz e sombras |
| Tudo volta `em_branco` | Grade desalinhada, ou folha realmente em branco | Confira a prévia; se preciso, baixe `margem_relativa` para 0,04 |
| "Prova longa demais" | Acima de 140 questões | Divida em dois cartões-resposta |
| Nota não aparece no boletim | Cartão avulso, sem aluno no QR | Gere os cartões pela aba Provas, a partir de uma turma |
| Dados somem no Cloud Run | SQLite em disco efêmero | Aponte `DATABASE_URL` para um Postgres |
| Câmera não abre no celular | Site em HTTP | A câmera exige HTTPS; use o envio de arquivos ou publique com TLS |
| Erro 404 na tela | Interface antiga contra servidor novo | Apague a pasta antiga e use esta inteira; navegue com Ctrl+F5 |
| "PDF protegido por senha" | Prova exportada com proteção | Salve uma cópia sem senha e envie de novo |
| Nota não entra no boletim | Cartão impresso antes da assinatura | Gere os cartões de novo na aba Provas |
| "A assinatura não confere" | Cartão de outra instalação, ou `SEGREDO_QR` mudou | Reimprima os cartões desta instalação |
| "As bolhas não formam uma grade regular" | Bolhas desalinhadas, ou algo redondo na folha | Confira a prévia; alinhe as bolhas no seu cartão |
| Detectou menos questões que a prova | Uma bolha ficou coberta ou fora do padrão | Veja a prévia: a linha faltante aparece sem marcação |
| A folha ganhou faixa branca | Nenhum canto tinha espaço livre para o QR | É o comportamento correto — nada do seu cartão foi coberto |
| `ModuleNotFoundError: database` | Versão antiga da pasta | O `database.py` virou o pacote `dados/`; use a pasta nova inteira |
| Menu lateral não aparece | `index.html` antigo em cache | Recarregue com Ctrl+F5 |
| Análise vazia | Nenhuma prova corrigida nesta escola | Corrija ao menos uma folha |
| "Encontrei apenas N bolhas" | Enviou a prova em vez do cartão-resposta | Envie o cartão em branco, com as bolhas impressas |
| Grade detectada errada na prévia | Layout fora do padrão de grade | Use o cartão do sistema, ou ajuste o seu para grade regular |
| "As margens não tinham espaço" | Conteúdo até a borda da folha | Normal: a folha ganhou borda branca e o conteúdo foi preservado |
| "X respostas ficaram de fora" | Questões sem conteúdo cadastrado | Classifique o conteúdo na aba Provas |
| Ano não aparece no comparativo | Turma sem ano escolar | Edite a turma e escolha o ano |
| Cartão com A–D lido errado | Grade assumindo 5 alternativas | Use "meu cartão-resposta": a detecção descobre quantas são |
| "Os quadrados dos cantos não foram encontrados" | Impressora cortou as âncoras de baixo | Reimprima com esta versão (margem de 15 mm) e em tamanho real |
| Conteúdo duplicado na análise | "Frações" e "fracao" digitados diferente | Use as sugestões do campo |
| Perdi o ticket de admin | O código só aparece na emissão | `python recuperar_admin.py` na pasta do sistema |
| Modo explicação vazio | Falta `static/ajuda.js` | Confira com `python conferir_arquivos.py` |
| Esqueci a senha | Não há recuperação por e-mail ainda | Crie outra conta pelo banco, ou apague a linha em `usuarios` |
| Perdi o ticket | O código só aparece na emissão | Peça outro a quem administra; o do admin também está no log do servidor |
| "Ticket já em uso por outra conta" | O código foi usado por outra pessoa | Peça um ticket próprio — eles não são compartilháveis |
| Ninguém consegue entrar | Ticket do admin perdido | Apague a linha do usuário em `usuarios` e recrie a conta inicial |
| "Não reconheci o arquivo" | Arquivo .doc/.docx | Exporte a prova como PDF antes de anexar |
| Tudo volta `rasura` | ROI pegando bolhas vizinhas | Reduza `roi_escala` para 0,50–0,55 |
| Cartão impresso com texto minúsculo | Falta fonte TTF no sistema | Instale `fonts-dejavu-core` (já está no Dockerfile) |
| Erro `libGL.so.1` | Alguém trocou por `opencv-python` | Volte para `opencv-python-headless` |
| `Container failed to start` | App não escuta em `$PORT` | Não fixe a porta; o `CMD` expande `${PORT}` |

---

## 19. Limitações conhecidas

- **O gabarito trafega em texto puro dentro do QR.** Um aluno pode ler o código
  com o celular e descobrir as respostas antes de entregar, ou gerar um QR
  próprio com as respostas que marcou — e agora também trocar o `aluno_id` para
  atribuir a nota a outra pessoa. Se a nota tiver peso real, assine o conteúdo
  com HMAC e valide no servidor.
- **A detecção de grade assume layout regular.** Bolhas em diagonal, tamanhos
  misturados ou alternativas em quadrado não são reconhecidas. A prévia existe
  justamente para isso aparecer antes da impressão.
- **A análise não separa provas diferentes com o mesmo conteúdo.** Uma prova
  fácil e uma difícil sobre frações entram no mesmo bolo. Para uma leitura mais
  fina seria preciso calibrar dificuldade por item (TRI), o que exige muito mais
  dados do que uma escola produz num bimestre.
- **Cada turma tem sua própria prova, mesmo quando o exame é idêntico.** Isso
  impede agregar a mesma questão entre turmas e reduz o tamanho das amostras por
  questão. Provas compartilhadas entre turmas resolveriam.
- **Todos os usuários veem tudo.** O login separa quem é de fora, mas não
  separa professores entre si: qualquer conta acessa todas as escolas e turmas
  cadastradas. Para uma escola só isso é razoável; para uma rede, seria preciso
  vincular turma a usuário.
- **Sem recuperação de senha.** Quem esquecer precisa de outra conta ou de
  acesso ao banco. Falta e-mail transacional para resolver direito.
- **O limite de tentativas de login é por instância.** Fica em memória: some no
  restart e não é compartilhado entre instâncias do Cloud Run. Freia o ataque
  simples, não um distribuído.
- **Rotações de 90°** não são corrigidas — só 180°. Fotos deitadas precisam ser
  giradas antes do envio.
- **Acima de 140 questões** a folha é recusada com orientação para dividir em
  dois cartões. Não é um teto escolhido a dedo: é onde a bolha deixaria de ser
  preenchível a caneta. Provas maiores exigiriam dois cartões, e aí o sistema
  precisaria saber juntar as duas folhas do mesmo aluno — o que hoje ele não faz,
  porque não há campo de identificação legível por máquina.
- **Sem persistência.** As notas existem só durante a sessão; a saída é o CSV.
  Para histórico, ligue um Firestore indexado por `prova_id`.
- **Lotes grandes numa requisição só** podem esbarrar no timeout do Cloud Run.
  Acima de ~40 fotos, considere Cloud Tasks + Cloud Storage.
