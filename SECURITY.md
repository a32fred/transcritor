# Política de segurança

## Versões suportadas

| Versão | Atualizações de segurança |
| --- | --- |
| 1.0.x | Sim |
| Outras versões | Não |

Use a versão de manutenção mais recente da série suportada e mantenha as dependências atualizadas.

## Relatar uma vulnerabilidade em particular

Envie um e-mail para **fredericocarlos.a32@gmail.com**, aos cuidados de Frederico Carlos,
com o assunto `[Segurança] transcritor`. **Não abra uma issue ou PR público com detalhes da falha.**

Inclua a versão afetada, sistema operacional, dependências relevantes, impacto estimado e passos
mínimos para reprodução. Prefira arquivos sintéticos e não envie gravações privadas, credenciais
ou informações pessoais desnecessárias.

A expectativa é confirmar o recebimento em até **5 dias úteis** e fornecer uma avaliação inicial
ou atualização em até **15 dias úteis**. São metas de um projeto mantido voluntariamente, não uma
garantia de atendimento. Sem resposta no prazo, reenvie o e-mail. A correção e a divulgação serão
combinadas com quem relatou, conforme a gravidade; não há prazo fixo para resolver toda ocorrência.

## Modelo de ameaça e limites

- **Execução local:** o programa processa áudio e vídeo na máquina do usuário e não envia áudio
  a serviços externos. O download inicial de modelos e a instalação de dependências requerem rede;
  com o modelo disponível localmente, a transcrição pode funcionar offline.
- **Servidor web sem autenticação:** por padrão, escuta em `127.0.0.1`. Não há autenticação.
  Expor a porta à rede, alterar o host para `0.0.0.0` ou publicar por proxy/túnel é responsabilidade
  do usuário e pode dar acesso a funcionalidades e dados a terceiros. Não o publique diretamente
  na internet; se necessário, providencie autenticação, controle de acesso e proteção de transporte
  externos. O padrão local não substitui uma barreira de segurança contra processos locais maliciosos.
- **Mídia não confiável:** a decodificação e a inspeção são delegadas ao `ffmpeg`/`ffprobe`.
  Arquivos malformados podem explorar falhas dessas ferramentas ou consumir muitos recursos.
  Mantenha-as atualizadas, não execute como administrador e considere isolamento e limites de
  recursos ao processar conteúdo de origem desconhecida.
- **Dados em disco:** arquivos de entrada, transcrições, temporários e histórico podem conter
  informações sensíveis. Proteja permissões, backups e acesso à máquina; não presuma criptografia
  ou apagamento seguro desses dados pelo programa.

Falhas em dependências também são relevantes: relate como afetam o transcritor e, quando cabível,
use o canal privado de segurança do componente afetado.
