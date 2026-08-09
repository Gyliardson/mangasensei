import { BookOpenText, FileImage, LoaderCircle, LockKeyhole, ScanText, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { ReaderWorkspace } from "./features/reader/ReaderWorkspace";
import { ApiError, type JobStatus, type StudyPage, fetchProtectedImage, uploadPage, waitForPage } from "./lib/api";
import { APPLICATION_VERSION, versionLabel } from "./version";

const acceptedTypes = ".jpg,.jpeg,.png,.webp";

export function App() {
  const [file, setFile] = useState<File | null>(null);
  const [phase, setPhase] = useState<"idle" | "uploading" | "processing" | "complete" | "error">("idle");
  const [jobStatus, setJobStatus] = useState<JobStatus | null>(null);
  const [page, setPage] = useState<StudyPage | null>(null);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const operation = useRef<AbortController | null>(null);

  useEffect(() => () => operation.current?.abort(), []);

  useEffect(() => () => {
    if (imageUrl) URL.revokeObjectURL(imageUrl);
  }, [imageUrl]);

  const reset = () => {
    operation.current?.abort();
    operation.current = null;
    if (imageUrl) URL.revokeObjectURL(imageUrl);
    setFile(null);
    setPhase("idle");
    setJobStatus(null);
    setPage(null);
    setImageUrl(null);
    setError(null);
  };

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!file) {
      setError("Selecione uma imagem antes de continuar.");
      setPhase("error");
      return;
    }
    const controller = new AbortController();
    operation.current?.abort();
    operation.current = controller;
    setError(null);
    setPhase("uploading");
    try {
      const upload = await uploadPage(file, controller.signal);
      setPhase("processing");
      setJobStatus("pending");
      const protectedImage = await fetchProtectedImage(
        upload.pageId,
        upload.capabilities.readImage,
        controller.signal,
      );
      setImageUrl(protectedImage);
      const result = await waitForPage(upload, controller.signal, setJobStatus);
      setPage(result);
      setPhase("complete");
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") return;
      setError(caught instanceof ApiError ? caught.message : "O processamento não pôde ser concluído.");
      setPhase("error");
    }
  };

  if (phase === "complete" && page && imageUrl) {
    return (
      <div className="app-shell">
        <Header />
        <ReaderWorkspace page={page} imageUrl={imageUrl} onReset={reset} />
        <Footer />
      </div>
    );
  }

  return (
    <div className="app-shell">
      <Header />

      <main id="conteudo" className="workspace">
        <section className="intro" aria-labelledby="page-title">
          <p className="eyebrow">Leitura assistida, sem alterar a página</p>
          <h1 id="page-title">Leia japonês no contexto</h1>
          <p className="lede">
            Envie uma página, selecione uma região e transforme cada fala em material de estudo.
          </p>
        </section>

        <form className="upload-panel" aria-labelledby="upload-title" onSubmit={submit}>
          <div className="section-index" aria-hidden="true">01</div>
          <div className="upload-copy">
            <div className="title-row">
              <ScanText aria-hidden="true" />
              <h2 id="upload-title">Escolha uma página</h2>
            </div>
            <p>JPEG, PNG ou WebP. Até 12 MiB e 25 megapixels.</p>
          </div>

          <label className="file-drop" htmlFor="page-image">
            <FileImage aria-hidden="true" />
            <span className="file-action">Selecionar imagem</span>
            <span className="file-detail">{file?.name ?? "ou arraste o arquivo para cá"}</span>
          </label>
          <input
            className="sr-only"
            id="page-image"
            name="page-image"
            type="file"
            accept={acceptedTypes}
            aria-label="Imagem da página"
            aria-describedby="upload-retention"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          />

          <button className="primary-button" type="submit" disabled={!file || phase === "uploading" || phase === "processing"}>
            {phase === "uploading" || phase === "processing" ? <LoaderCircle className="spinner" aria-hidden="true" /> : <ScanText aria-hidden="true" />}
            {phase === "uploading" ? "Enviando imagem" : phase === "processing" ? statusText(jobStatus) : "Analisar página"}
          </button>

          {phase === "uploading" || phase === "processing" ? (
            <button className="cancel-button" type="button" onClick={reset}>
              <X aria-hidden="true" /> Cancelar
            </button>
          ) : null}

          {error ? <p className="form-error" role="alert">{error}</p> : null}

          <p id="upload-retention" className="retention">
            <LockKeyhole aria-hidden="true" />
            Originais e resultados são excluídos automaticamente após 24 horas.
          </p>
        </form>

        <aside className="study-preview" aria-labelledby="preview-title">
          <BookOpenText aria-hidden="true" />
          <div>
            <p className="eyebrow">Próxima etapa</p>
            <h2 id="preview-title">Texto, leitura e nuance lado a lado</h2>
            <p>As regiões reconhecidas aparecerão sobre a imagem original e poderão ser abertas por teclado.</p>
          </div>
        </aside>
      </main>

      <Footer />
    </div>
  );
}

function Header() {
  return (
    <header className="site-header">
      <a className="brand" href="/" aria-label="MangaSensei, página inicial">
        <span className="brand-mark" aria-hidden="true">間</span>
        <span>MangaSensei</span>
      </a>
      <p className="privacy-note"><LockKeyhole aria-hidden="true" /> Processamento local e temporário</p>
    </header>
  );
}

function Footer() {
  return (
    <footer>
      <span>{versionLabel(APPLICATION_VERSION)}</span>
      <span>GPL-3.0-only</span>
      <span>Copyright (C) 2026 Gyliardson Keitison</span>
    </footer>
  );
}

function statusText(status: JobStatus | null): string {
  const labels: Partial<Record<JobStatus, string>> = {
    pending: "Aguardando worker",
    claimed: "Preparando análise",
    processing_ocr: "Reconhecendo texto",
    processing_linguistics: "Analisando japonês",
    processing_gemini: "Gerando contexto",
    retryable_failure: "Tentando novamente",
  };
  return status ? labels[status] ?? "Processando página" : "Processando página";
}
