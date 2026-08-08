# Contribuindo com o MangaSensei

Idiomas: [English](CONTRIBUTING.md) | [Português](CONTRIBUTING.pt-BR.md) | [日本語](CONTRIBUTING.ja.md) | [Español](CONTRIBUTING.es.md)

Obrigado pelo interesse no MangaSensei. Contribuições, issues e pull requests são bem-vindos em **English, Português, 日本語 ou Español**. Ao participar, você concorda em seguir o [Código de Conduta](CODE_OF_CONDUCT.pt-BR.md).

## Código de Conduta

Leia o [Código de Conduta](CODE_OF_CONDUCT.pt-BR.md). Assédio e comportamento excludente não são aceitos. Violações devem ser reportadas de forma privada conforme as orientações do próprio documento.

## Segurança

Se encontrar uma vulnerabilidade, **não abra uma issue pública**. Use o processo de divulgação responsável da [Política de Segurança](SECURITY.pt-BR.md).

## Primeiros Passos

1. Leia o [README principal](README.pt-BR.md), também disponível em [English](README.md), [日本語](README.ja.md) e [Español](README.es.md).
2. Confirme que consegue executar os quality gates locais relevantes antes de alterar código.
3. Verifique issues, discussions e pull requests existentes para evitar trabalho duplicado.
4. Para mudanças não triviais, abra uma discussion ou issue primeiro para alinhar a abordagem.

## Fluxo de Desenvolvimento

Usamos `main` protegida e pull requests focados:

- Faça fork se for contribuidor externo ou crie uma branch focada se tiver acesso de escrita.
- Mantenha mudanças pequenas e revisáveis; prefira várias PRs focadas a uma PR gigante.
- Atualize sua branch com a `main` mais recente antes da revisão final.
- Abra a PR contra `main` e preencha o template.
- Checks obrigatórios do CI e conversas de review não resolvidas precisam estar concluídos antes do merge.

### Nome de branch

Use um nome curto e descritivo:

```text
feat/reading-order
fix/idempotency-conflict
docs/jmdict-license
```

### Conventional Commits

Commits e títulos de PR seguem [Conventional Commits]:

```text
<type>(<scope>): <description>
```

Exemplos:

```text
feat(worker): persist dictionary version and digest
fix(cli): allow jmdict/models verify without database credentials
docs: add multilingual community guidance
test(runner): cover public error code mapping
```

Tipos comuns: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`, `build`, `style`, `revert`.

O repositório usa squash merge, então o título revisado da PR se torna o título do commit final na `main`.

## Estrutura e Convenções

```text
backend/   API Python, worker, migrations, OCR e linguística
frontend/  SPA React, componentes do leitor e testes Playwright
docs/      Notas de versão e artefatos visuais
tests/     Testes unitários e de integração do backend
```

Diretrizes:

- Backend: mantenha módulos focados, type hints, tipagem estrita e a cobertura configurada.
- Frontend: siga os padrões React/TypeScript existentes e considere acessibilidade.
- Nunca comite dados gerados, pesos de modelos, `.env`, credenciais ou artefatos de build.
- Mantenha [`.gitignore`](.gitignore) e [`.dockerignore`](.dockerignore) alinhados quando locais de artefatos mudarem.
- Preserve o desenho local-first e a imagem original enviada.

## Quality Gates Locais

Antes de enviar mudanças, execute os gates relevantes. Os workflows atuais do GitHub Actions são a fonte de verdade para o CI obrigatório.

```powershell
# consistência do repositório
.\.venv\Scripts\python.exe scripts/version.py check
.\.venv\Scripts\python.exe scripts/check_markdown_links.py

# backend
.\.venv\Scripts\python.exe -m ruff check backend/src tests scripts
.\.venv\Scripts\python.exe -m mypy backend/src
$env:MANGASENSEI_TEST_DATABASE_URL='postgresql+psycopg://mangasensei:mangasensei@127.0.0.1:55432/mangasensei'
.\.venv\Scripts\python.exe -m pytest --cov

# frontend
npm run lint
npm run typecheck
npm run test:coverage
npm run build
npm run e2e
```

Observações:

- Testes de integração do backend exigem PostgreSQL. Use o banco local de desenvolvimento ou o serviço PostgreSQL do Docker Compose.
- O smoke test opcional de OCR usa pesos reais e é ignorado por padrão. Defina `MANGASENSEI_RUN_OCR_SMOKE=1` e `MANGASENSEI_MODEL_CACHE` para habilitá-lo.
- Testes E2E do frontend iniciam automaticamente um servidor Playwright.
- O CI também constrói a distribuição Python, instala o wheel em ambiente limpo, constrói a imagem Docker de produção e executa verificações de segurança.

## Documentação e Traduções

Os arquivos em inglês são os arquivos de integração canônicos reconhecidos pelo GitHub, mas a documentação voltada a contribuidores é mantida em inglês, português do Brasil, japonês e espanhol.

Quando README ou orientações compartilhadas mudarem:

- Atualize as versões de idioma afetadas na mesma PR sempre que possível.
- Mantenha comandos, paths, versões e contratos técnicos idênticos entre traduções.
- Não crie traduções paralelas de `LICENSE`, `CHANGELOG.md`, `THIRD_PARTY_NOTICES.md`, manifests ou workflows.
- Referências destinadas ao leitor devem usar links Markdown navegáveis.

## Versionamento e Releases

O projeto usa [Semantic Versioning](https://semver.org/) e mantém release notes curadas no [changelog](CHANGELOG.md). A versão do projeto Python em [`pyproject.toml`](pyproject.toml) é autoritativa; o tooling do repositório mantém os mirrors necessários.

Não edite mirrors de versão manualmente. Se [`scripts/version.py`](scripts/version.py) continuar sendo o tooling atual, use:

```powershell
.\.venv\Scripts\python.exe scripts/version.py set 0.2.0
```

O comando atualiza mirrors mecânicos, mas não escreve release notes. Promova e revise manualmente as entradas relevantes de `[Unreleased]` em [`CHANGELOG.md`](CHANGELOG.md).

Depois que o commit de release passar pelo CI e entrar na `main`, uma tag `vX.Y.Z` correspondente aciona o [workflow de release](.github/workflows/release.yml). Mantenedores decidem quando uma release está pronta; contribuidores não devem criar tags de release em PRs normais.

## Reportando Problemas

Use os templates de issue quando possível. Você pode escrever em qualquer um dos quatro idiomas suportados.

- **Bug reports**: inclua versão ou commit, sistema operacional, passos de reprodução e logs relevantes.
- **Feature requests**: descreva motivação, comportamento esperado e impactos em privacidade/API/storage.

## Pull Requests

Preencha o template da PR. Uma boa PR:

- Explica o que muda e por quê.
- Referencia a issue relacionada quando aplicável, por exemplo `Closes #123`.
- Lista os testes/checks executados.
- Passa pelos quality gates obrigatórios.
- Atualiza a documentação afetada.
- Evita cleanup não relacionado.

Mantenedores revisarão funcionalidade, segurança, compatibilidade e aderência ao projeto. Podemos pedir mudanças, dividir uma contribuição grande em PRs menores ou fechar uma proposta fora de escopo com explicação.

## Licença

Ao contribuir, você concorda que suas contribuições serão licenciadas sob os mesmos termos do projeto (`GPL-3.0-only`). Veja a [licença](LICENSE).

[Conventional Commits]: https://www.conventionalcommits.org/en/v1.0.0/
