<div align="center">

# MangaSensei

**Lee manga. Entiende japonés. Mantén tus páginas en local.**

Un entorno de estudio centrado en la privacidad para convertir páginas de manga en material interactivo de japonés con OCR local, lingüística determinista y explicaciones opcionales por IA.

[English](README.md) · [Português](README.pt-BR.md) · [日本語](README.ja.md) · [Español](README.es.md)

</div>

[![CI](https://github.com/Gyliardson/mangasensei/actions/workflows/ci.yml/badge.svg)](https://github.com/Gyliardson/mangasensei/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Gyliardson/mangasensei?sort=semver&display_name=tag)](https://github.com/Gyliardson/mangasensei/releases)
[![Licencia](https://img.shields.io/badge/license-GPL--3.0--only-8f1d2c)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11-315b7d)](docs/versions.md)
[![React](https://img.shields.io/badge/React-19-315b7d)](docs/versions.md)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-18.4-315b7d)](docs/versions.md)

MangaSensei extrae texto japonés de páginas de manga, enriquece el resultado con datos lingüísticos locales y lo presenta en un lector responsivo sin alterar la imagen original. Los pesos de OCR y los datos derivados de JMdict permanecen en local y no se incluyen en Git ni en la imagen distribuible. Gemini es opcional.

El contenido japonés puede estudiarse con **explicaciones contextuales en Portugués de Brasil (`pt-BR`) o Inglés (`en`)**. El idioma de estudio se elige de forma explícita y es independiente del locale de la interfaz almacenado en el navegador. La interfaz admite Inglés (`en`) y Portugués de Brasil (`pt-BR`), usa inglés por defecto en un estado nuevo o inválido del navegador y persiste localmente una selección explícita. Los significados deterministas del JMdict local siguen en inglés en ambos modos de estudio, y cambiar solo el idioma de estudio reutiliza el OCR y el análisis lingüístico japonés ya completados. Consulta el [contrato de idiomas de estudio](docs/study-languages.md) para los límites exactos.

La versión actual de desarrollo está registrada en [`VERSION`](VERSION).

## ¿Por qué MangaSensei?

| Local-first | Imagen original preservada | Diseñado para estudiar |
| --- | --- | --- |
| OCR, modelos y datos del diccionario son locales por defecto. | La página subida se conserva sin modificaciones y las capas de estudio se renderizan por separado. | Furigana, vocabulario, idioma de estudio, datos lingüísticos y explicaciones contextuales se organizan alrededor de la lectura. |

## Vista previa del lector

<table>
  <tr>
    <td width="68%" align="center"><a href="docs/assets/reader-desktop-chromium.png"><img src="docs/assets/reader-desktop-chromium.png" alt="Lector de escritorio de MangaSensei"></a><br><sub>Escritorio</sub></td>
    <td width="32%" align="center"><a href="docs/assets/reader-mobile-chromium.png"><img src="docs/assets/reader-mobile-chromium.png" alt="Lector móvil de MangaSensei"></a><br><sub>Móvil</sub></td>
  </tr>
</table>

## Funciones

| Área | Capacidad |
| --- | --- |
| Carga | Subida segura de imágenes con idempotencia, idioma de estudio explícito `pt-BR`/`en` y capabilities HMAC por página |
| OCR | Subconjunto local de Manga Image Translator con modelos verificados por checksum |
| Lingüística | Tokenización Sudachi e índice JMdict normalizado en inglés generado desde una fuente verificada |
| Gemini | Explicaciones contextuales estructuradas opcionales en `pt-BR`/`en`, con control de presupuesto y `store=False` |
| Lector | SPA React con Blob autenticado, overlays SVG responsivos, furigana, preferencias independientes de locale de interfaz `en`/`pt-BR` e idioma de estudio, y tarjetas de vocabulario |
| Operación | Cola PostgreSQL, recuperación de leases, retención, readiness y métricas |

## Arquitectura

```mermaid
flowchart TD
    subgraph Cliente["Capa Cliente"]
        Browser["React Reader SPA"]
    end
    subgraph API["Capa API"]
        FastAPI["Aplicación FastAPI"]
        Capabilities["Capabilities por Página"]
        Static["Assets Estáticos del Frontend"]
    end
    subgraph Worker["Capa Worker"]
        Runner["Worker Runner"]
        Queue["Cola PostgreSQL"]
        OCR["Motor OCR Local"]
        Linguistics["Sudachi + JMdict"]
        Gemini["Análisis Gemini Opcional"]
    end
    subgraph Storage["Capa de Datos"]
        DB[("PostgreSQL 18.4")]
        Files["Almacenamiento de Imágenes por Contenido"]
        Models["Modelos OCR Locales"]
        Dictionary["JMdict JSON Local"]
    end
    Browser --> FastAPI
    FastAPI --> Capabilities
    FastAPI --> Static
    FastAPI --> DB
    FastAPI --> Files
    Queue --> DB
    Runner --> Queue
    Runner --> OCR
    Runner --> Linguistics
    Runner --> Gemini
    OCR --> Models
    Linguistics --> Dictionary
    Runner --> DB
    Runner --> Files
    classDef client fill:#2563eb,stroke:#1d4ed8,color:#ffffff;
    classDef service fill:#475569,stroke:#334155,color:#ffffff;
    classDef data fill:#059669,stroke:#047857,color:#ffffff;
    class Browser client;
    class FastAPI,Capabilities,Static,Runner,Queue,OCR,Linguistics,Gemini service;
    class DB,Files,Models,Dictionary data;
```

## Superficie de la API

| Método | Ruta | Propósito |
| --- | --- | --- |
| `POST` | `/api/v1/pages` | Sube una página de manga japonés y crea análisis con `studyLanguage` opcional (`pt-BR` por defecto o `en`) |
| `GET` | `/api/v1/pages/{page_id}` | Consulta estado, metadatos persistidos de idioma y datos de estudio completados usando el token de página |
| `GET` | `/api/v1/pages/{page_id}/image` | Devuelve la imagen original mediante una respuesta Blob autenticada |
| `POST` | `/api/v1/pages/{page_id}/reprocess` | Encola análisis o regeneración solo del idioma de estudio usando una capability de reprocesamiento |
| `GET` | `/health` | Health check del proceso |
| `GET` | `/ready` | Verificación de base de datos, almacenamiento y schema |
| `GET` | `/metrics` | Métricas Prometheus |

## Ejecución Local

Requisitos:

| Herramienta | Versión compatible |
| --- | --- |
| Python | `3.11.x` |
| Node.js | objetivo `24 LTS`; `22.12+` compatible para tooling local |
| Docker | `28.x` |
| uv | Necesario para dependencias Python y gestión del lockfile |

Consulta [`docs/versions.md`](docs/versions.md) para la matriz revisada de la stack.

Instala dependencias y prepara los artefactos locales:

```powershell
py -3.11 -m uv sync --extra ocr
npm install
Copy-Item .env.example .env
.\.venv\Scripts\mangasensei.exe models download
.\.venv\Scripts\mangasensei.exe jmdict download
```

Genera secretos y configúralos en `.env` antes de ejecutar cualquier cosa que use base de datos, cola o API. Sustituye `POSTGRES_PASSWORD`, la contraseña dentro de `MANGASENSEI_DATABASE_URL` y el valor de `MANGASENSEI_CAPABILITY_PEPPERS` por valores aleatorios nuevos:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Gemini es opcional. Deja `GOOGLE_API_KEY` sin definir o en blanco para ejecutar el worker solo con OCR y lingüística locales; configura una clave no vacía para habilitar enriquecimiento contextual en el idioma de estudio seleccionado. Cuando está habilitado, solo se envían el texto OCR y candidatos léxicos mínimos por región (`id`, `surface`, `lemma`, `reading`). La imagen original, los significados del diccionario y el dataset JMdict local no se envían. Con Gemini deshabilitado, el OCR/tokenización japoneses y el vocabulario JMdict en inglés siguen disponibles, mientras que la traducción y explicación contextuales pueden estar ausentes.

Ejecuta con Docker Compose:

```powershell
docker compose up --build
```

Ejecuta los quality gates locales:

```powershell
.\.venv\Scripts\python.exe scripts/version.py check
.\.venv\Scripts\python.exe scripts/check_markdown_links.py
.\.venv\Scripts\python.exe -m ruff check backend/src tests scripts
.\.venv\Scripts\python.exe -m mypy backend/src
.\.venv\Scripts\python.exe -m pytest --cov
npm run lint
npm run typecheck
npm run test:coverage
npm run build
npm run e2e
```

## Estructura del Repositorio

```text
backend/      API Python, worker, migraciones, OCR y lingüística
frontend/     SPA React, componentes del lector y pruebas Playwright
docs/         Notas de versión y artefactos visuales
tests/        Pruebas unitarias e integración del backend
var/          Datos locales de runtime ignorados por Git
```

## Actividad del Proyecto

[![Colaboradores](https://img.shields.io/github/contributors/Gyliardson/mangasensei)](https://github.com/Gyliardson/mangasensei/graphs/contributors)
[![Actividad de commits](https://img.shields.io/github/commit-activity/m/Gyliardson/mangasensei)](https://github.com/Gyliardson/mangasensei/commits/main)
[![Issues abiertas](https://img.shields.io/github/issues/Gyliardson/mangasensei)](https://github.com/Gyliardson/mangasensei/issues)
[![Discussions](https://img.shields.io/github/discussions/Gyliardson/mangasensei)](https://github.com/Gyliardson/mangasensei/discussions)

| Explora | Enlace |
| --- | --- |
| Colaboradores | [Personas que construyen MangaSensei](https://github.com/Gyliardson/mangasensei/graphs/contributors) |
| Historial | [Historial de commits](https://github.com/Gyliardson/mangasensei/commits/main) |
| Roadmap y bugs | [Issues](https://github.com/Gyliardson/mangasensei/issues) |
| Ideas y preguntas | [Discussions](https://github.com/Gyliardson/mangasensei/discussions) |
| Seguridad | [Resumen de seguridad](https://github.com/Gyliardson/mangasensei/security) |
| Releases | [GitHub Releases](https://github.com/Gyliardson/mangasensei/releases) |

## Contribución

Las contribuciones son bienvenidas en **English, Português, 日本語 o Español**. Lee la guía en tu idioma preferido:

[English](CONTRIBUTING.md) · [Português](CONTRIBUTING.pt-BR.md) · [日本語](CONTRIBUTING.ja.md) · [Español](CONTRIBUTING.es.md)

Sigue también el [Código de Conducta](CODE_OF_CONDUCT.es.md). Para vulnerabilidades, utiliza el canal privado descrito en la [Política de Seguridad](SECURITY.es.md).

## Datos y Licencias

El código fuente de MangaSensei usa GPL-3.0-only. Los datos derivados de JMdict se generan localmente desde fuentes de terceros verificadas y siguen sujetos a los términos EDRDG / CC BY-SA. Los pesos de los modelos OCR son artefactos locales y este repositorio no los redistribuye.

Consulta [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) para atribuciones, checksums y referencias de origen.

## Licencia

Copyright (C) 2026 Gyliardson Keitison. MangaSensei está licenciado bajo [GPL-3.0-only](LICENSE). Los componentes de terceros conservan sus respectivos avisos.
