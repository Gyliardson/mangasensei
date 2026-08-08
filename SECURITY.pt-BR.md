# Política de Segurança

Idiomas: [English](SECURITY.md) | [Português](SECURITY.pt-BR.md) | [日本語](SECURITY.ja.md) | [Español](SECURITY.es.md)

O MangaSensei é um software privacy-first que processa páginas de mangá enviadas pelo usuário, dados locais de OCR/modelos e requisições opcionais a IA externa. Relatos de segurança são bem-vindos e devem ser tratados de forma privada.

## Versões Suportadas

Até a publicação da primeira GitHub Release pública, correções de segurança têm como alvo a branch `main` atual. Depois do início das releases estáveis, correções serão aplicadas à linha de release suportada mais recente, com patch releases quando apropriado.

A página de [GitHub Releases](https://github.com/Gyliardson/mangasensei/releases) é a fonte de verdade para versões publicadas.

## Reportando uma Vulnerabilidade

**Não abra uma issue pública para vulnerabilidades de segurança.**

Use o Private Vulnerability Reporting do GitHub:

- [Reportar uma vulnerabilidade de forma privada](https://github.com/Gyliardson/mangasensei/security/advisories/new)

Inclua, quando possível:

- Versão, commit e plataforma afetados.
- Reprodução mínima ou descrição precisa.
- Impacto observado ou esperado.
- Logs relevantes com secrets, tokens, conteúdo de mangá e dados pessoais removidos.
- Correção ou mitigação sugerida, se houver.

Nosso objetivo é confirmar o recebimento em até 5 dias úteis. Manteremos o pesquisador informado durante a investigação e coordenaremos a divulgação antes de publicar detalhes quando a vulnerabilidade for confirmada.

## Escopo de Segurança

Relatos são especialmente úteis para problemas envolvendo:

- Bypass de capabilities ou autorização.
- Exposição de páginas de mangá enviadas ou dados retidos de usuários.
- Validação de upload, path traversal ou manipulação insegura de arquivos.
- Isolamento de fila/worker, propriedade de jobs ou falhas de retenção.
- Exposição de secrets, credenciais ou API keys.
- Riscos de supply chain em dependências, build, release ou GitHub Actions.
- Regressões de privilégios ou isolamento de filesystem em containers.
- Transmissão externa inesperada de dados, inclusive no comportamento da integração Gemini.

Em geral ficam **fora de escopo**, salvo quando a integração do próprio MangaSensei cria a vulnerabilidade:

- Secrets colocados intencionalmente pelo usuário no próprio `.env` local.
- Vulnerabilidades que existem apenas em uma modificação local não suportada.
- Vulnerabilidades de serviços/dependências de terceiros que não podem ser mitigadas no MangaSensei.
- O simples fato de pesos locais de OCR ou dados JMdict existirem na máquina do usuário.

## Processo de Divulgação

1. O relato é enviado de forma privada.
2. Um mantenedor avalia severidade, reprodução e boundaries afetadas.
3. A correção e cobertura de regressão são preparadas sem expor publicamente detalhes de exploração.
4. Os gates obrigatórios de CI/segurança são executados no SHA exato da correção.
5. Quando necessário, uma patch release de segurança é preparada.
6. O advisory é publicado com divulgação coordenada e crédito ao pesquisador, salvo pedido de anonimato.

## Safe Harbor

Pesquisa de segurança de boa-fé e divulgação responsável são bem-vindas. Não buscaremos ação legal contra pesquisadores que:

- Reportem pelo canal privado acima.
- Evitem violar a privacidade de outras pessoas.
- Não destruam, corrompam ou retenham dados que não lhes pertencem.
- Não explorem uma vulnerabilidade além do razoavelmente necessário para demonstrá-la.
- Deem aos mantenedores tempo razoável para investigar e corrigir antes da divulgação pública.

Esta declaração de safe harbor se aplica a pesquisas de boa-fé contra o próprio MangaSensei; ela não autoriza testes em sistemas ou dados de terceiros.
