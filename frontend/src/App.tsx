import {
  ArrowDown,
  ArrowUp,
  BookOpenText,
  FileImage,
  LoaderCircle,
  LockKeyhole,
  ScanText,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { DocumentReader } from "./features/reader/DocumentReader";
import { ReaderWorkspace } from "./features/reader/ReaderWorkspace";
import {
  ApiError,
  type DocumentUploadData,
  type JobStatus,
  type StudyPage,
  type UploadData,
  fetchProtectedImage,
  reprocessStudyLanguage,
  uploadDocument,
  uploadPage,
  waitForPage,
} from "./lib/api";
import {
  type DictionaryLanguage,
  loadDictionaryLanguagePreference,
  saveDictionaryLanguagePreference,
} from "./lib/dictionaryLanguage";
import { documentMessagesFor, type DocumentUiMessages } from "./lib/documentUiMessages";
import {
  type StudyLanguage,
  isStudyLanguage,
  loadStudyLanguagePreference,
  saveStudyLanguagePreference,
} from "./lib/studyLanguage";
import { messagesFor, type UiMessages } from "./lib/uiMessages";
import {
  type UiLocale,
  isUiLocale,
  loadUiLocalePreference,
  saveUiLocalePreference,
} from "./lib/uiLocale";
import { APPLICATION_VERSION, versionLabel } from "./version";
import "./document-upload.css";

const acceptedTypes = ".jpg,.jpeg,.png,.webp";

type LocalErrorCode = "select_image_first" | "unexpected_processing";
type DisplayError =
  | { readonly kind: "local"; readonly code: LocalErrorCode }
  | { readonly kind: "api"; readonly code: string };
type LanguageMutation = "study" | null;
type AppPhase = "idle" | "uploading" | "processing" | "complete" | "document" | "error";

export function App() {
  const [files, setFiles] = useState<File[]>([]);
  const [phase, setPhase] = useState<AppPhase>("idle");
  const [jobStatus, setJobStatus] = useState<JobStatus | null>(null);
  const [page, setPage] = useState<StudyPage | null>(null);
  const [pageAccess, setPageAccess] = useState<UploadData | null>(null);
  const [documentAccess, setDocumentAccess] = useState<DocumentUploadData | null>(null);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [error, setError] = useState<DisplayError | null>(null);
  const [uiLocale, setUiLocale] = useState<UiLocale>(() => loadUiLocalePreference());
  const [preferredStudyLanguage, setPreferredStudyLanguage] = useState<StudyLanguage>(() =>
    loadStudyLanguagePreference(),
  );
  const [preferredDictionaryLanguage] = useState<DictionaryLanguage>(() => {
    const normalized = loadDictionaryLanguagePreference();
    saveDictionaryLanguagePreference(normalized);
    return normalized;
  });
  const [languageMutation, setLanguageMutation] = useState<LanguageMutation>(null);
  const [studyLanguageErrorCode, setStudyLanguageErrorCode] = useState<string | null>(null);
  const operation = useRef<AbortController | null>(null);
  const messages = messagesFor(uiLocale);
  const documentMessages = documentMessagesFor(uiLocale);

  useEffect(() => () => operation.current?.abort(), []);

  useEffect(() => () => {
    if (imageUrl) URL.revokeObjectURL(imageUrl);
  }, [imageUrl]);

  useEffect(() => {
    document.documentElement.lang = uiLocale;
    document.querySelector<HTMLAnchorElement>(".skip-link")?.replaceChildren(messages.skipLink);
    document
      .querySelector<HTMLMetaElement>('meta[name="description"]')
      ?.setAttribute("content", messages.documentDescription);
  }, [messages, uiLocale]);

  const reset = () => {
    operation.current?.abort();
    operation.current = null;
    if (imageUrl) URL.revokeObjectURL(imageUrl);
    setFiles([]);
    setPhase("idle");
    setJobStatus(null);
    setPage(null);
    setPageAccess(null);
    setDocumentAccess(null);
    setImageUrl(null);
    setError(null);
    setLanguageMutation(null);
    setStudyLanguageErrorCode(null);
  };

  const changeUiLocale = (value: string) => {
    if (!isUiLocale(value)) return;
    setUiLocale(value);
    saveUiLocalePreference(value);
  };

  const changePreferredStudyLanguage = (value: string) => {
    if (!isStudyLanguage(value)) return;
    setPreferredStudyLanguage(value);
    saveStudyLanguagePreference(value);
  };

  const selectFiles = (candidates: readonly File[]) => {
    setFiles(Array.from(candidates));
    setError(null);
    if (phase === "error") setPhase("idle");
  };

  const handleDrop = (event: React.DragEvent<HTMLLabelElement>) => {
    event.preventDefault();
    selectFiles(Array.from(event.dataTransfer.files));
  };

  const moveFile = (index: number, delta: -1 | 1) => {
    setFiles((current) => {
      const destination = index + delta;
      if (destination < 0 || destination >= current.length) return current;
      const reordered = [...current];
      const [moved] = reordered.splice(index, 1);
      if (!moved) return current;
      reordered.splice(destination, 0, moved);
      return reordered;
    });
  };

  const removeFile = (index: number) => {
    setFiles((current) => current.filter((_, candidateIndex) => candidateIndex !== index));
  };

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (files.length === 0) {
      setError({ kind: "local", code: "select_image_first" });
      setPhase("error");
      return;
    }
    const controller = new AbortController();
    operation.current?.abort();
    operation.current = controller;
    setError(null);
    setPhase("uploading");
    try {
      if (files.length > 1) {
        const document = await uploadDocument(files, preferredStudyLanguage, controller.signal);
        setDocumentAccess(document);
        setPhase("document");
        operation.current = null;
        return;
      }

      const file = files[0];
      if (!file) throw new ApiError("invalid_image");
      const upload = await uploadPage(file, preferredStudyLanguage, controller.signal);
      setPageAccess(upload);
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
      setPreferredStudyLanguage(result.studyLanguage);
      saveStudyLanguagePreference(result.studyLanguage);
      saveDictionaryLanguagePreference("en");
      setPhase("complete");
      operation.current = null;
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") return;
      setPageAccess(null);
      setDocumentAccess(null);
      setError(
        caught instanceof ApiError
          ? { kind: "api", code: caught.code }
          : { kind: "local", code: "unexpected_processing" },
      );
      setPhase("error");
    }
  };

  const changeStudyLanguage = async (target: StudyLanguage) => {
    if (!page || !pageAccess || languageMutation) return;
    setPreferredStudyLanguage(target);
    saveStudyLanguagePreference(target);
    setStudyLanguageErrorCode(null);
    if (target === page.studyLanguage) return;

    const previousLanguage = page.studyLanguage;
    const controller = new AbortController();
    operation.current = controller;
    setLanguageMutation("study");
    try {
      await reprocessStudyLanguage(pageAccess, target, controller.signal);
      const result = await waitForPage(pageAccess, controller.signal, setJobStatus);
      setPage(result);
      setPreferredStudyLanguage(result.studyLanguage);
      saveStudyLanguagePreference(result.studyLanguage);
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") return;
      setPreferredStudyLanguage(previousLanguage);
      saveStudyLanguagePreference(previousLanguage);
      setStudyLanguageErrorCode(caught instanceof ApiError ? caught.code : "study_language_update_failed");
    } finally {
      if (operation.current === controller) operation.current = null;
      setLanguageMutation(null);
    }
  };

  const studyLanguageError = studyLanguageErrorCode
    ? studyLanguageErrorCode === "study_language_update_failed"
      ? messages.studyLanguageUpdateFailed
      : messages.apiError(studyLanguageErrorCode)
    : null;

  if (phase === "document" && documentAccess) {
    return (
      <div className="app-shell">
        <Header uiLocale={uiLocale} messages={messages} onUiLocaleChange={changeUiLocale} />
        <DocumentReader
          access={documentAccess}
          uiLocale={uiLocale}
          preferredStudyLanguage={preferredStudyLanguage}
          preferredDictionaryLanguage={preferredDictionaryLanguage}
          onPreferredStudyLanguageChange={setPreferredStudyLanguage}
          onPreferredDictionaryLanguageChange={() => undefined}
          onReset={reset}
        />
        <Footer />
      </div>
    );
  }

  if (phase === "complete" && page && imageUrl) {
    return (
      <div className="app-shell">
        <Header uiLocale={uiLocale} messages={messages} onUiLocaleChange={changeUiLocale} />
        <ReaderWorkspace
          page={page}
          imageUrl={imageUrl}
          uiLocale={uiLocale}
          preferredStudyLanguage={preferredStudyLanguage}
          preferredDictionaryLanguage={preferredDictionaryLanguage}
          languageMutation={languageMutation}
          studyLanguageError={studyLanguageError}
          dictionaryLanguageError={null}
          onStudyLanguageChange={changeStudyLanguage}
          onDictionaryLanguageChange={() => undefined}
          onReset={reset}
        />
        <Footer />
      </div>
    );
  }

  return (
    <div className="app-shell">
      <Header uiLocale={uiLocale} messages={messages} onUiLocaleChange={changeUiLocale} />

      <main id="conteudo" className="workspace">
        <section className="intro" aria-labelledby="page-title">
          <p className="eyebrow">{messages.introEyebrow}</p>
          <h1 id="page-title">{messages.introTitle}</h1>
          <p className="lede">{messages.introLede}</p>
        </section>

        <form className="upload-panel" aria-labelledby="upload-title" onSubmit={submit}>
          <div className="section-index" aria-hidden="true">01</div>
          <div className="upload-copy">
            <div className="title-row">
              <ScanText aria-hidden="true" />
              <h2 id="upload-title">{messages.uploadTitle}</h2>
            </div>
            <p>{messages.uploadRequirements}</p>
            <label className="upload-study-language">
              <span>{messages.studyLanguageLabel}</span>
              <select
                aria-label={messages.studyLanguageLabel}
                value={preferredStudyLanguage}
                onChange={(event) => changePreferredStudyLanguage(event.currentTarget.value)}
              >
                <option value="pt-BR">{messages.studyLanguageName("pt-BR")}</option>
                <option value="en">{messages.studyLanguageName("en")}</option>
              </select>
              <small>{messages.studyLanguageNote}</small>
            </label>
          </div>

          <label
            className="file-drop"
            htmlFor="page-image"
            onDragOver={(event) => {
              event.preventDefault();
              event.dataTransfer.dropEffect = "copy";
            }}
            onDrop={handleDrop}
          >
            <FileImage aria-hidden="true" />
            <span className="file-action">{messages.selectImage}</span>
            <span className="file-detail">
              {files.length > 0 ? documentMessages.selectedPages(files.length) : messages.fileDropHint}
            </span>
          </label>
          <input
            className="sr-only"
            id="page-image"
            name="page-image"
            type="file"
            multiple
            accept={acceptedTypes}
            aria-label={messages.pageImageAria}
            aria-describedby="upload-retention page-order-hint"
            onChange={(event) => selectFiles(Array.from(event.target.files ?? []))}
          />

          {files.length > 0 ? (
            <section className="selected-pages" aria-label={documentMessages.selectedPages(files.length)}>
              <p id="page-order-hint">{documentMessages.pageOrderHint}</p>
              <ol>
                {files.map((file, index) => (
                  <li key={`${file.name}:${file.size}:${file.lastModified}:${index}`}>
                    <span className="selected-page-name"><strong>{index + 1}</strong> {file.name}</span>
                    <div className="selected-page-actions" role="group" aria-label={file.name}>
                      <button
                        type="button"
                        className="icon-button"
                        aria-label={documentMessages.movePageUp(file.name)}
                        disabled={index === 0}
                        onClick={() => moveFile(index, -1)}
                      >
                        <ArrowUp aria-hidden="true" />
                      </button>
                      <button
                        type="button"
                        className="icon-button"
                        aria-label={documentMessages.movePageDown(file.name)}
                        disabled={index === files.length - 1}
                        onClick={() => moveFile(index, 1)}
                      >
                        <ArrowDown aria-hidden="true" />
                      </button>
                      <button
                        type="button"
                        className="icon-button"
                        aria-label={documentMessages.removePage(file.name)}
                        onClick={() => removeFile(index)}
                      >
                        <Trash2 aria-hidden="true" />
                      </button>
                    </div>
                  </li>
                ))}
              </ol>
              <button className="text-button" type="button" onClick={() => selectFiles([])}>
                {documentMessages.clearPages}
              </button>
            </section>
          ) : null}

          <button
            className="primary-button"
            type="submit"
            disabled={files.length === 0 || phase === "uploading" || phase === "processing"}
          >
            {phase === "uploading" || phase === "processing"
              ? <LoaderCircle className="spinner" aria-hidden="true" />
              : <ScanText aria-hidden="true" />}
            {phase === "uploading"
              ? messages.uploadingImage
              : phase === "processing"
                ? messages.jobStatus(jobStatus)
                : documentMessages.analyzePages(files.length)}
          </button>

          {phase === "uploading" || phase === "processing" ? (
            <button className="cancel-button" type="button" onClick={reset} aria-describedby="upload-retention">
              <X aria-hidden="true" /> {messages.stopFollowing}
            </button>
          ) : null}

          {error ? (
            <p className="form-error" role="alert">
              {displayError(messages, documentMessages, error)}
            </p>
          ) : null}

          <p id="upload-retention" className="retention">
            <LockKeyhole aria-hidden="true" />
            {messages.uploadRetention}
          </p>
        </form>

        <aside className="study-preview" aria-labelledby="preview-title">
          <BookOpenText aria-hidden="true" />
          <div>
            <p className="eyebrow">{messages.previewEyebrow}</p>
            <h2 id="preview-title">{messages.previewTitle}</h2>
            <p>{messages.previewBody}</p>
          </div>
        </aside>
      </main>

      <Footer />
    </div>
  );
}

function Header({
  uiLocale,
  messages,
  onUiLocaleChange,
}: {
  readonly uiLocale: UiLocale;
  readonly messages: UiMessages;
  readonly onUiLocaleChange: (value: string) => void;
}) {
  return (
    <header className="site-header">
      <a className="brand" href="/" aria-label={messages.brandHomeAria}>
        <span className="brand-mark" aria-hidden="true">間</span>
        <span>MangaSensei</span>
      </a>
      <div className="header-actions">
        <p className="privacy-note"><LockKeyhole aria-hidden="true" /> {messages.privacyNote}</p>
        <label className="ui-locale-picker">
          <span>{messages.uiLocaleLabel}</span>
          <select
            aria-label={messages.uiLocaleLabel}
            value={uiLocale}
            onChange={(event) => onUiLocaleChange(event.currentTarget.value)}
          >
            <option value="en">{messages.localeNames.en}</option>
            <option value="pt-BR">{messages.localeNames["pt-BR"]}</option>
          </select>
        </label>
      </div>
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

function displayError(
  messages: UiMessages,
  documentMessages: DocumentUiMessages,
  error: DisplayError,
): string {
  if (error.kind === "api") {
    if (error.code === "document_page_limit_exceeded") return documentMessages.documentPageLimit;
    if (error.code === "document_byte_limit_exceeded") return documentMessages.documentByteLimit;
    if (error.code === "document_pixel_limit_exceeded") return documentMessages.documentPixelLimit;
    if (error.code.startsWith("document_")) return documentMessages.documentUploadFailed;
    return messages.apiError(error.code);
  }
  if (error.code === "select_image_first") return messages.selectImageFirst;
  return messages.unexpectedProcessingError;
}