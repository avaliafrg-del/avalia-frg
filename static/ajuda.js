/* ==================================================================
   ajuda.js — o conteúdo do Modo explicação.
   ------------------------------------------------------------------
   Fica num arquivo próprio, e não dentro do index.html, por um motivo
   prático: este texto vai ser corrigido com frequência — a cada dúvida
   que um professor trouxer — e mexer num arquivo de texto é bem menos
   arriscado do que mexer no meio do HTML e do JavaScript da tela.

   Estrutura:
     AJUDA[painel] = {
       titulo, resumo,
       passos: [...]        o caminho, na ordem de fazer
       campos: [...]        o que cada campo significa
       cuidados: [...]      o que costuma dar errado
       perguntas: [...]     dúvidas que aparecem de verdade
     }
   ================================================================== */

const AJUDA = {

  /* ================================================================ */
  turmas: {
    titulo: "Turmas e alunos",
    resumo: `Aqui ficam guardadas a escola, as turmas e os nomes dos alunos.
      É o primeiro passo: sem uma turma cadastrada, o sistema não tem em quem
      lançar as notas. Você faz isso uma vez no começo do ano e não precisa
      repetir a cada prova.`,
    passos: [
      { titulo: "Escolha ou cadastre a escola",
        texto: `No campo <b>Escola</b>, veja se a sua já aparece na lista. Se não
          aparecer, escreva o nome em <b>Nova escola</b> e clique em
          <b>Adicionar escola</b>. Se você é uma conta de escola, a sua já vem
          selecionada e não há o que escolher.` },
      { titulo: "Crie a turma",
        texto: `Escreva o apelido da turma em <b>Nova turma</b> — por exemplo
          “4º ano B” ou “Turma da manhã”. Depois escolha o <b>Ano escolar</b> na
          lista ao lado e clique em <b>Adicionar turma</b>.` },
      { titulo: "Cole a lista de alunos",
        texto: `Na caixa da direita, cole os nomes <b>um por linha</b>. Dá para
          copiar direto de uma planilha: selecione a coluna dos nomes no Excel,
          copie e cole aqui. Depois clique em <b>Adicionar à turma</b>.` },
      { titulo: "Confira a lista",
        texto: `Os nomes aparecem abaixo. Se algum entrou errado, clique em
          <b>remover</b> ao lado dele e cole o nome certo.` },
    ],
    campos: [
      { nome: "Escola", texto: `A instituição. Todas as turmas e notas ficam
        organizadas dentro dela.` },
      { nome: "Nova turma", texto: `O apelido, do jeito que a escola chama:
        “4º B”, “Turma da tarde”. Serve para você encontrar a turma.` },
      { nome: "Ano escolar", texto: `A série — 4º ano, 2ª série do médio. É
        <b>diferente</b> do apelido, e é o que permite comparar o mesmo ano entre
        escolas depois. Uma turma sem ano escolar fica de fora do comparativo.` },
      { nome: "Alunos", texto: `Um nome por linha. Nomes repetidos são ignorados,
        então você pode colar a mesma lista de novo sem duplicar ninguém.` },
    ],
    cuidados: [
      `<b>Apelido e ano escolar são coisas diferentes.</b> “4º B” é o apelido;
       “4º ano” é o ano escolar. Preencher só o apelido faz a turma sumir das
       comparações entre escolas.`,
      `<b>Cole os nomes um por linha.</b> Se vierem todos numa linha só,
       separados por vírgula, o sistema vai entender como o nome de um aluno só.`,
      `<b>Confira a grafia.</b> O nome que você digitar aqui é o que sai impresso
       no cartão-resposta e no boletim.`,
    ],
    perguntas: [
      { q: "Preciso cadastrar de novo a cada prova?",
        a: `Não. A turma é cadastrada uma vez e serve para todas as provas do ano.` },
      { q: "Um aluno entrou na turma depois. E agora?",
        a: `Basta colar o nome dele na caixa e clicar em Adicionar à turma. Os
          que já estavam continuam lá.` },
      { q: "Posso apagar um aluno que saiu?",
        a: `Pode, clicando em <b>remover</b>. Atenção: as notas dele saem junto,
          e isso não tem volta.` },
    ],
  },

  /* ================================================================ */
  provas: {
    titulo: "Criar prova",
    resumo: `Aqui você registra o gabarito e gera os cartões-resposta para
      imprimir. O sistema cria <b>uma folha para cada aluno</b>, com o nome dele
      já impresso e um QR Code próprio — é esse código que depois faz a nota cair
      no aluno certo, sem ninguém digitar nada.`,
    passos: [
      { titulo: "Preencha os dados da prova",
        texto: `<b>Título</b> (ex.: “Avaliação bimestral”), <b>Disciplina</b> e
          <b>Quantas questões</b>. O título aparece impresso no topo do cartão.` },
      { titulo: "Marque o gabarito",
        texto: `Para cada questão, clique na letra da resposta certa. A letra
          marcada fica verde. Se errar, clique de novo para desmarcar, ou em
          outra letra para trocar.` },
      { titulo: "Ou cole o gabarito de uma vez",
        texto: `Numa prova longa, use o campo <b>“Ou cole tudo de uma vez”</b>:
          digite as respostas em sequência, como <code>ABCDEABCDE</code>, e
          clique em Aplicar. O sistema preenche a grade inteira e ajusta o número
          de questões sozinho.` },
      { titulo: "Classifique o conteúdo (importante)",
        texto: `Ao lado de cada questão há um campo de <b>conteúdo</b>: escreva o
          que ela cobra — “Frações”, “Ortografia”, “Interpretação de texto”. É
          esse campo que permite ao sistema dizer depois “a turma errou frações”,
          em vez de apenas “erraram a questão 15”. Sem ele, a aba de Análise fica
          quase vazia.` },
      { titulo: "Anexe a prova, se quiser",
        texto: `Em <b>Anexar o documento da prova</b>, mande o PDF da prova. O
          arquivo final sai com a prova primeiro e os cartões depois, na ordem de
          imprimir.` },
      { titulo: "Gere e imprima",
        texto: `Clique em <b>Criar prova e gerar cartões</b>. Um PDF é baixado com
          uma folha por aluno. <b>Imprima em tamanho real (100%)</b> — nunca em
          “ajustar à página”.` },
    ],
    campos: [
      { nome: "Quantas questões", texto: `De 1 a 140 numa folha só. Acima de 20,
        a grade se reorganiza em colunas automaticamente.` },
      { nome: "Gabarito", texto: `A resposta certa de cada questão. Só o sistema
        conhece; o QR do cartão não carrega as respostas.` },
      { nome: "Conteúdo da questão", texto: `O assunto cobrado. Use sempre a mesma
        escrita — o campo sugere os conteúdos que você já usou, para evitar
        “Frações”, “fracao” e “FRAÇÕES” virarem três coisas diferentes.` },
      { nome: "Usar o meu cartão-resposta", texto: `Se a escola já tem um modelo
        próprio de cartão, envie o PDF em branco. O sistema carimba só as marcas
        que precisa nas margens e descobre sozinho onde estão as bolhas.` },
    ],
    cuidados: [
      `<b>Imprima em tamanho real.</b> “Ajustar à página” encolhe a folha, desloca
       os quadrados pretos dos cantos e a leitura falha.`,
      `<b>Entregue a folha certa para cada aluno.</b> Cada cartão tem o nome
       impresso e um código diferente. Trocar as folhas troca as notas.`,
      `<b>Confira a prévia</b> antes de imprimir a turma inteira, principalmente
       se estiver usando o cartão próprio da escola.`,
      `<b>Não dobre nem escreva sobre os quadrados pretos dos cantos.</b> São eles
       que permitem endireitar a foto.`,
    ],
    perguntas: [
      { q: "Posso mudar o gabarito depois de gerar os cartões?",
        a: `O gabarito fica guardado no sistema, então dá para corrigir. Mas se a
          prova já foi aplicada, converse com a coordenação antes: mudar o
          gabarito muda todas as notas já lançadas.` },
      { q: "Preciso classificar o conteúdo de todas as questões?",
        a: `Não é obrigatório, e a correção funciona sem isso. Mas a Análise
          pedagógica só enxerga as questões classificadas — as outras aparecem
          num aviso de “ficaram de fora”.` },
      { q: "Meu cartão tem 4 alternativas, não 5. Funciona?",
        a: `Funciona. Use a opção <b>“Usar o meu cartão-resposta”</b>: o sistema
          detecta quantas alternativas existem na sua folha.` },
    ],
  },

  /* ================================================================ */
  corrigir: {
    titulo: "Corrigir provas",
    resumo: `Você fotografa as folhas e o sistema faz o resto. Não precisa digitar
      o gabarito nem o nome do aluno: os dois saem do QR Code impresso no cartão.
      Há três jeitos de enviar — pela câmera, arrastando fotos, ou um arquivo só
      com a turma inteira.`,
    passos: [
      { titulo: "Pela câmera do computador ou celular",
        texto: `Clique em <b>Abrir câmera</b>, encaixe a folha inteira no quadro e
          clique em <b>Capturar e corrigir</b>. A nota aparece na hora. Quando o
          QR é reconhecido, o aviso na tela fica verde.` },
      { titulo: "Arrastando fotos",
        texto: `Tire as fotos com o celular, passe para o computador e arraste
          todas de uma vez para a área pontilhada. Depois clique em
          <b>Corrigir</b>.` },
      { titulo: "Um arquivo com a turma inteira",
        texto: `Se o cartão foi digitalizado no scanner da escola, você tem um PDF
          com várias páginas: mande esse arquivo em <b>Corrigir a turma inteira de
          uma vez</b>. Também aceita um ZIP com as fotos. O sistema avisa quantas
          folhas encontrou e quanto tempo vai levar.` },
      { titulo: "Confira o resultado",
        texto: `A nota aparece com o detalhe questão a questão: verde para certa,
          vermelho para errada, cinza para em branco e amarelo para rasura.` },
      { titulo: "Baixe o relatório, se precisar",
        texto: `Em <b>Corrigidos nesta sessão</b> há os botões de PDF e planilha.
          Para contas de escola e da secretaria isso é opcional, porque as notas
          já estão no boletim. Para o <b>convidado</b> é essencial: nada fica
          guardado.` },
    ],
    campos: [
      { nome: "Abrir câmera", texto: `Usa a câmera do aparelho. Só funciona em
        endereço seguro (https) ou no próprio computador onde o sistema roda —
        é uma regra do navegador, não do sistema.` },
      { nome: "Arraste as fotos aqui", texto: `Aceita várias fotos de uma vez,
        em JPG ou PNG.` },
      { nome: "Corrigir a turma inteira", texto: `Um PDF de scanner ou um ZIP de
        fotos. Até 120 folhas por arquivo.` },
    ],
    cuidados: [
      `<b>A folha inteira precisa aparecer na foto</b>, com os quatro quadrados
       pretos visíveis. É por eles que o sistema endireita a imagem.`,
      `<b>Luz vindo de cima.</b> Evite a sua própria sombra sobre o papel. Não
       precisa estar perfeitamente reta — o sistema corrige a inclinação.`,
      `<b>Peça caneta azul ou preta</b> e bolha preenchida por inteiro. O sistema
       lê traço fraco e marca incompleta, mas quanto mais nítido, mais seguro.`,
      `<b>Se aparecer “confira este resultado”</b>, os cantos não foram
       encontrados e a leitura pode ter saído torta. Vale refazer a foto.`,
    ],
    perguntas: [
      { q: "O aluno marcou duas alternativas. O que acontece?",
        a: `A questão é registrada como <b>rasura</b> e não conta como acerto. Ela
          aparece em amarelo no detalhe, para você decidir o que fazer.` },
      { q: "E se o aluno não respondeu uma questão?",
        a: `Fica como <b>em branco</b>, em cinza. O sistema separa isso de
          “errou”, porque não saber e não ter chegado na questão são coisas
          diferentes.` },
      { q: "Corrigi a folha errada. Dá para refazer?",
        a: `Dá. Corrigir a folha do mesmo aluno de novo <b>substitui</b> a nota
          anterior, em vez de criar uma segunda.` },
      { q: "Uma foto do lote saiu ruim. Perco tudo?",
        a: `Não. As demais são corrigidas normalmente, e a folha problemática volta
          identificada — “página 7” — para você achar o papel e refazer só ela.` },
    ],
  },

  /* ================================================================ */
  boletim: {
    titulo: "Boletim da turma",
    resumo: `A lista de notas de uma prova, com quem já entregou e quem falta, e o
      percentual de acerto de cada questão.`,
    passos: [
      { titulo: "Escolha a prova",
        texto: `Na lista do topo, selecione a avaliação. Só aparecem as provas da
          turma selecionada na aba Turmas.` },
      { titulo: "Leia os números do topo",
        texto: `Média da turma, quantas folhas foram corrigidas, maior e menor
          nota.` },
      { titulo: "Veja quem falta",
        texto: `Alunos sem folha corrigida continuam na lista, marcados como “sem
          folha”. É o jeito de saber quem faltou ou cuja prova se perdeu.` },
      { titulo: "Olhe o acerto por questão",
        texto: `A barra de cada questão mostra quanto da turma acertou. Vermelho
          abaixo de 50%: vale retomar aquele conteúdo.` },
      { titulo: "Exporte",
        texto: `<b>Baixar planilha (CSV)</b> gera um arquivo que abre no Excel,
          para lançar no diário ou somar com outras avaliações.` },
    ],
    cuidados: [
      `<b>Média baixa numa questão não quer dizer que a turma é fraca.</b> Pode ser
       enunciado confuso ou conteúdo que ainda não foi dado. Olhe a questão antes
       de concluir.`,
    ],
    perguntas: [
      { q: "O aluno aparece sem nota, mas eu corrigi a folha dele.",
        a: `Provavelmente a folha era de outro aluno, ou o QR não foi lido e a
          correção saiu como avulsa. Confira em “Corrigidos nesta sessão” se o
          nome dele apareceu.` },
    ],
  },

  /* ================================================================ */
  alunos: {
    titulo: "Histórico do aluno",
    resumo: `O desempenho de cada aluno ao longo do ano: todas as provas, a média,
      e se ele está melhorando ou caindo.`,
    passos: [
      { titulo: "Escolha o aluno",
        texto: `Clique no nome na lista à esquerda. Ao lado de cada um já aparece
          a média e quantas provas fez.` },
      { titulo: "Leia a evolução",
        texto: `O gráfico mostra as notas na ordem das provas. A linha vermelha
          tracejada marca o 6,0, para você ver de relance quando o aluno ficou
          abaixo.` },
      { titulo: "Veja a tendência",
        texto: `Ao lado do nome pode aparecer <b>melhorando</b>, <b>em queda</b> ou
          <b>estável</b>. Isso só é calculado a partir de quatro provas: com duas
          ou três notas, qualquer conclusão seria chute.` },
    ],
    cuidados: [
      `<b>Uma nota baixa isolada não é tendência.</b> Aluno tem dia ruim, prova
       difícil, dor de cabeça. O valor desta tela está no conjunto.`,
    ],
    perguntas: [
      { q: "Por que não aparece a tendência do meu aluno?",
        a: `Porque ele tem menos de quatro provas corrigidas. O sistema prefere
          não dizer nada a dizer algo que os dados não sustentam.` },
    ],
  },

  /* ================================================================ */
  analise: {
    titulo: "Análise pedagógica",
    resumo: `Esta é a tela que responde “o que precisa ser retomado”. Ela junta
      todas as provas corrigidas e mostra quais <b>conteúdos</b> a escola está
      errando mais — não apenas quais questões.`,
    passos: [
      { titulo: "Escolha o recorte",
        texto: `Selecione a escola e, se quiser, o ano escolar, a disciplina e o
          período. Olhar “a escola toda” mistura 1º e 9º ano e diz pouco.` },
      { titulo: "Leia as orientações",
        texto: `No topo aparecem frases prontas, do tipo <i>“2º ano: Frações teve
          33% de acerto em 4 questões, com 60 respostas. Recomenda-se retomada
          prioritária.”</i> Há um botão para copiar e enviar à escola.` },
      { titulo: "Veja o acerto por conteúdo",
        texto: `As barras vão do conteúdo mais frágil para o mais consolidado.
          Vermelho é crítico, amarelo é atenção, verde está adequado.` },
      { titulo: "Compare os anos escolares",
        texto: `A tabela mostra cada ano da escola com sua média e o conteúdo mais
          frágil. É por aí que a coordenação decide onde concentrar formação.` },
      { titulo: "Olhe o erro mais comum",
        texto: `Na lista de questões, quando muitos alunos marcam <b>a mesma</b>
          alternativa errada, aparece “erro concentrado”. Isso não é chute: é um
          raciocínio errado compartilhado, e dá para saber qual.` },
    ],
    cuidados: [
      `<b>Amostra pequena não vira conclusão.</b> Abaixo de 25 respostas o número
       aparece marcado como “amostra pequena” e não gera orientação. Um sistema
       que recomenda com base em ruído perde a confiança de quem lê.`,
      `<b>Questões sem conteúdo cadastrado ficam de fora.</b> A tela avisa quantas
       são. Se esse número for alto, volte em Criar prova e classifique.`,
    ],
    perguntas: [
      { q: "A tela está vazia. O que faltou?",
        a: `Uma de duas coisas: não há prova corrigida nesta escola, ou as questões
          não têm conteúdo cadastrado. O aviso no topo diz qual é o caso.` },
      { q: "O que significa “erro concentrado”?",
        a: `Que pelo menos metade dos erros daquela questão caiu na mesma
          alternativa. Chute espalharia os erros entre as opções; concentração
          indica que a turma aprendeu algo errado da mesma forma.` },
    ],
  },

  /* ================================================================ */
  comparativo: {
    titulo: "Comparar escolas",
    resumo: `Coloca duas ou mais escolas lado a lado, no mesmo recorte. Exclusiva
      da secretaria. Todo número vem com <b>margem de erro</b>, e a diferença
      entre escolas só é chamada de diferença quando resiste a um teste
      estatístico.`,
    passos: [
      { titulo: "Marque as escolas",
        texto: `Selecione pelo menos duas na lista.` },
      { titulo: "Defina o recorte",
        texto: `Escolha o ano escolar e a disciplina. Comparar tudo junto mistura
          séries diferentes e não permite conclusão.` },
      { titulo: "Leia “o que dá para afirmar”",
        texto: `Cada par de escolas recebe uma frase. Quando a diferença cabe
          dentro do acaso, a frase diz <b>“empate técnico”</b> com todas as
          letras.` },
      { titulo: "Veja conteúdo a conteúdo",
        texto: `A tabela mostra o acerto de cada escola em cada conteúdo. Só
          aparecem conteúdos classificados em <b>todas</b> as escolas escolhidas.` },
    ],
    cuidados: [
      `<b>O sinal de ± é a parte mais importante da tela.</b> Uma escola com 18
       alunos e 71% de acerto não está acima de outra com 120 alunos e 68%: a
       primeira tem margem de ±10 pontos.`,
      `<b>Esta tela não diz qual escola é melhor.</b> Perfil de entrada dos alunos,
       contexto do bairro e rotatividade de professores não estão nestes dados.
       Ela mostra onde há diferença de desempenho, e só.`,
    ],
    perguntas: [
      { q: "Uma escola tem 62% e a outra 58%. Por que aparece empate?",
        a: `Porque com o tamanho dessas amostras, 4 pontos de diferença cabem
          dentro do acaso. Repetindo a prova, o resultado poderia se inverter.` },
      { q: "Sou de uma escola e não vejo esta aba.",
        a: `Ela é exclusiva da secretaria. Contas de escola veem apenas os dados
          da própria escola.` },
    ],
  },

  /* ================================================================ */
  acessos: {
    titulo: "Usuários e acessos",
    resumo: `Só a secretaria vê esta aba, no grupo <b>Sistema</b> do menu. É aqui
      que se criam as contas dos três tipos — administrador, escola e convidado —
      e se distribuem os tickets, o código que cada pessoa precisa para entrar,
      além do email e da senha. No fim da página ficam o backup e a limpeza.`,
    passos: [
      { titulo: "Crie a conta",
        texto: `Informe nome, email, senha e o <b>papel</b>. Para uma conta de
          escola, é obrigatório escolher a escola: sem vínculo, a pessoa entra e
          não vê nada.` },
      { titulo: "Emita o ticket",
        texto: `Clique em emitir, descrevendo para quem é (“Prof. Carlos –
          Matemática”). <b>O código aparece uma única vez.</b> Copie e entregue à
          pessoa; depois só fica guardado o embaralhado dele.` },
      { titulo: "Revogue quando alguém sair",
        texto: `Clicar em <b>revogar</b> corta o acesso na hora e derruba a sessão
          aberta, sem precisar trocar a senha de ninguém.` },
    ],
    campos: [
      { nome: "Administrador", texto: `A secretaria. Faz tudo: cadastra, corrige,
        analisa, compara escolas e cria usuários.` },
      { nome: "Escola (membro)", texto: `Corrige as provas da própria escola e vê
        os resultados dela. Não altera cadastro nem enxerga outras escolas.` },
      { nome: "Convidado", texto: `Uso individual do professor. Monta o gabarito,
        corrige e baixa o relatório — nada é guardado no sistema.` },
    ],
    cuidados: [
      `<b>Anote o ticket no momento em que ele aparece.</b> Ele não pode ser
       recuperado: se perder, é preciso revogar e emitir outro.`,
      `<b>Cada pessoa precisa do próprio ticket.</b> Ele se prende à primeira
       conta que o usar, então repassar o código a um colega não dá acesso a ele.`,
      `<b>Limpar os dados apaga escolas, turmas, alunos e notas.</b> Contas e
       tickets continuam. Use só para preparar uma demonstração.`,
    ],
    perguntas: [
      { q: "Perdi o ticket de administrador. Estou trancado para fora?",
        a: `Não. Na pasta do sistema, rode <code>python recuperar_admin.py</code>
          no computador onde ele está instalado. O script emite um ticket novo.
          Exige acesso ao arquivo do sistema, o que é uma proteção em si.` },
      { q: "Posso ter mais de um administrador?",
        a: `Pode. Crie a conta com papel Administrador e emita um ticket para ela.` },
    ],
  },
};
