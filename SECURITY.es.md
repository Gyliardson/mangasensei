# Política de Seguridad

Idiomas: [English](SECURITY.md) | [Português](SECURITY.pt-BR.md) | [日本語](SECURITY.ja.md) | [Español](SECURITY.es.md)

MangaSensei es software privacy-first que procesa páginas de manga proporcionadas por usuarios, datos locales de OCR/modelos y solicitudes opcionales a IA externa. Los reportes de seguridad son bienvenidos y deben tratarse de forma privada.

## Versiones Soportadas

Hasta que se publique la primera GitHub Release pública, las correcciones de seguridad tienen como objetivo la rama `main` actual. Cuando comiencen las releases estables, las correcciones se aplicarán a la línea de release soportada más reciente, usando patch releases cuando corresponda.

La página de [GitHub Releases](https://github.com/Gyliardson/mangasensei/releases) es la fuente de verdad para versiones publicadas.

## Reportar una Vulnerabilidad

**No abras una issue pública para vulnerabilidades de seguridad.**

Usa GitHub Private Vulnerability Reporting:

- [Reportar una vulnerabilidad de forma privada](https://github.com/Gyliardson/mangasensei/security/advisories/new)

Incluye, cuando sea posible:

- Versión, commit y plataforma afectados.
- Una reproducción mínima o descripción precisa.
- El impacto observado o esperado.
- Logs relevantes sin secrets, tokens, contenido de manga ni datos personales.
- Una corrección o mitigación sugerida, si la tienes.

Nuestro objetivo es confirmar la recepción en un máximo de 5 días laborables. Mantendremos informado al reportero durante la investigación y coordinaremos la divulgación antes de publicar detalles cuando la vulnerabilidad sea confirmada.

## Alcance de Seguridad

Los reportes son especialmente útiles para problemas relacionados con:

- Bypass de capabilities o autorización.
- Exposición de páginas de manga subidas o datos retenidos de usuarios.
- Validación de uploads, path traversal o manejo inseguro de archivos.
- Aislamiento de queue/worker, ownership de jobs o fallos de retención.
- Exposición de secrets, credenciales o API keys.
- Riesgos de supply chain en dependencias, build, release o GitHub Actions.
- Regresiones en privilegios o aislamiento del filesystem de containers.
- Transmisión externa inesperada de datos, incluido el comportamiento de la integración Gemini.

Normalmente quedan **fuera de alcance**, salvo que la propia integración de MangaSensei cree la vulnerabilidad:

- Secrets colocados intencionadamente por el usuario en su propio `.env` local.
- Vulnerabilidades que solo existen en una modificación local no soportada.
- Vulnerabilidades de servicios/dependencias de terceros que MangaSensei no puede mitigar.
- El simple hecho de que pesos locales de OCR o datos JMdict existan en la máquina del usuario.

## Proceso de Divulgación

1. El reporte se envía de forma privada.
2. Un maintainer evalúa severidad, reproducibilidad y boundaries afectados.
3. Se prepara la corrección y cobertura de regresión sin publicar detalles de explotación.
4. Los gates obligatorios de CI/seguridad se ejecutan sobre el SHA exacto de la corrección.
5. Cuando sea necesario, se prepara una security patch release.
6. El advisory se publica mediante divulgación coordinada y con crédito al reportero salvo que solicite anonimato.

## Safe Harbor

La investigación de seguridad de buena fe y la divulgación responsable son bienvenidas. No emprenderemos acciones legales contra investigadores que:

- Reporten mediante el canal privado anterior.
- Eviten violar la privacidad de otras personas.
- No destruyan, corrompan ni retengan datos que no les pertenecen.
- No exploten una vulnerabilidad más allá de lo razonablemente necesario para demostrarla.
- Den a los maintainers un tiempo razonable para investigar y corregir antes de una divulgación pública.

Esta declaración de safe harbor se aplica a investigación de buena fe sobre MangaSensei; no autoriza pruebas sobre sistemas o datos de terceros.
