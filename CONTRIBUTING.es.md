# Contribuir a MangaSensei

Idiomas: [English](CONTRIBUTING.md) | [Português](CONTRIBUTING.pt-BR.md) | [日本語](CONTRIBUTING.ja.md) | [Español](CONTRIBUTING.es.md)

Gracias por tu interés en MangaSensei. Las contribuciones, issues y pull requests son bienvenidos en **English, Português, 日本語 o Español**. Al participar aceptas seguir el [Código de Conducta](CODE_OF_CONDUCT.es.md).

## Código de Conducta

Lee el [Código de Conducta](CODE_OF_CONDUCT.es.md). El acoso y el comportamiento excluyente no son aceptados. Si observas una infracción, utiliza las vías privadas de reporte descritas allí.

## Seguridad

Si encuentras una vulnerabilidad, **no abras una issue pública**. Sigue el proceso de divulgación responsable de la [Política de Seguridad](SECURITY.es.md).

## Primeros Pasos

1. Lee el [README en español](README.es.md), también disponible en [English](README.md), [Português](README.pt-BR.md) y [日本語](README.ja.md).
2. Asegúrate de poder ejecutar los quality gates locales relevantes antes de modificar código.
3. Revisa issues, discussions y pull requests existentes para evitar trabajo duplicado.
4. Para cambios no triviales, abre primero una discussion o issue para acordar el enfoque.

## Flujo de Desarrollo

Usamos una rama `main` protegida y pull requests enfocados:

- Haz fork del repositorio si eres colaborador externo, o crea una rama enfocada si tienes acceso de escritura.
- Mantén los cambios pequeños y fáciles de revisar. Es preferible dividir una contribución grande en varias PRs enfocadas.
- Actualiza tu rama con la `main` más reciente antes de solicitar la revisión final.
- Abre una PR contra `main` y completa la plantilla.
- Los checks obligatorios del CI y las conversaciones de review pendientes deben resolverse antes del merge.

### Nombres de ramas

Usa nombres cortos y descriptivos:

```text
feat/reading-order
fix/idempotency-conflict
docs/jmdict-license
```

### Conventional Commits

Los commits y títulos de PR siguen [Conventional Commits]:

```text
<type>(<scope>): <description>
```

Ejemplos:

```text
feat(worker): persist dictionary version and digest
fix(cli): allow jmdict/models verify without database credentials
docs: add multilingual community guidance
test(runner): cover public error code mapping
```

Tipos comunes: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`, `build`, `style`, `revert`.

El repositorio usa squash merge, por lo que el título revisado de la PR se convierte en el título del commit final en `main`.

## Estructura y Convenciones

```text
backend/   API Python, worker, migraciones, OCR y lingüística
frontend/  SPA React, componentes del lector y pruebas Playwright
docs/      Notas de versión y artefactos visuales
tests/     Pruebas unitarias e integración del backend
```

Directrices:

- Backend: mantén los módulos enfocados, usa type hints, conserva el tipado estricto y el requisito de cobertura configurado.
- Frontend: sigue los patrones React/TypeScript existentes y ten en cuenta accesibilidad.
- Nunca subas datos generados, pesos de modelos, archivos `.env`, credenciales o artefactos de build.
- Mantén [`.gitignore`](.gitignore) y [`.dockerignore`](.dockerignore) alineados cuando cambien las ubicaciones de artefactos de runtime.
- Preserva el diseño local-first y la imagen original subida.

## Quality Gates Locales

Antes de enviar cambios, ejecuta los gates relevantes. Los workflows actuales de GitHub Actions son la fuente de verdad para el CI obligatorio.

```powershell
# consistencia del repositorio
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

Notas:

- Las pruebas de integración del backend requieren PostgreSQL. Usa la base local de desarrollo o el servicio PostgreSQL de Docker Compose.
- El smoke test opcional de OCR carga pesos reales y se omite por defecto. Define `MANGASENSEI_RUN_OCR_SMOKE=1` y `MANGASENSEI_MODEL_CACHE` para habilitarlo.
- Las pruebas E2E del frontend inician automáticamente un servidor Playwright.
- El CI también construye la distribución Python, instala el wheel en un entorno limpio, construye la imagen Docker de producción y ejecuta verificaciones de seguridad.

## Documentación y Traducciones

Los archivos en inglés son los archivos canónicos de integración que GitHub reconoce, pero la documentación para colaboradores se mantiene en inglés, portugués de Brasil, japonés y español.

Cuando cambie el README o una guía compartida:

- Actualiza las versiones de idioma afectadas en la misma PR cuando sea práctico.
- Mantén comandos, rutas, versiones y contratos técnicos idénticos entre traducciones.
- No crees traducciones paralelas de `LICENSE`, `CHANGELOG.md`, `THIRD_PARTY_NOTICES.md`, manifests o workflows como nuevas fuentes de verdad.
- Las referencias de archivos para lectores deben usar enlaces Markdown navegables.

## Versionado y Releases

El proyecto usa [Semantic Versioning](https://semver.org/) y mantiene release notes revisadas en el [changelog](CHANGELOG.md). La versión del proyecto Python en [`pyproject.toml`](pyproject.toml) es autoritativa y el tooling del repositorio sincroniza los mirrors necesarios.

No edites los mirrors de versión manualmente. Si [`scripts/version.py`](scripts/version.py) sigue siendo el tooling actual, usa:

```powershell
.\.venv\Scripts\python.exe scripts/version.py set 0.2.0
```

El comando actualiza mirrors mecánicos, pero no escribe release notes. Promueve y revisa manualmente las entradas relevantes de `[Unreleased]` en [`CHANGELOG.md`](CHANGELOG.md).

Después de que el commit de release pase el CI y entre en `main`, una tag `vX.Y.Z` correspondiente activa el [workflow de release](.github/workflows/release.yml). Los maintainers deciden cuándo una release está lista; los colaboradores no deben crear tags de release en PRs normales.

## Reportar Problemas

Usa las plantillas de issue cuando sea posible. Puedes escribir en cualquiera de los cuatro idiomas soportados.

- **Bug reports**: incluye versión o commit, sistema operativo, pasos de reproducción y logs relevantes.
- **Feature requests**: describe motivación, comportamiento esperado e impacto en privacidad/API/storage.

## Pull Requests

Completa la plantilla de PR. Una buena PR:

- Explica qué cambia y por qué.
- Referencia la issue relacionada cuando aplique, por ejemplo `Closes #123`.
- Lista los tests/checks ejecutados.
- Pasa los quality gates obligatorios.
- Actualiza la documentación afectada.
- Evita cleanup no relacionado.

Los maintainers revisarán funcionalidad, seguridad, compatibilidad y encaje con el proyecto. Podemos solicitar cambios, dividir una contribución grande en PRs menores o cerrar una propuesta fuera de alcance explicando el motivo.

## Licencia

Al contribuir aceptas que tus contribuciones se licencian bajo los mismos términos del proyecto (`GPL-3.0-only`). Consulta la [licencia](LICENSE).

[Conventional Commits]: https://www.conventionalcommits.org/en/v1.0.0/
