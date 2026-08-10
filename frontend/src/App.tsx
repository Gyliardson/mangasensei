import { BookOpenText, FileImage, LoaderCircle, LockKeyhole, ScanText, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { ReaderWorkspace } from "./features/reader/ReaderWorkspace";
import {
  ApiError,
  type JobStatus,
  type StudyPage,
  type UploadData,
  fetchProtectedImage,
  reprocessDictionaryLanguage,
  reprocessStudyLanguage,
  uploadPage,
  waitForPage,
} from "./lib/api";
import {
  type DictionaryLanguage,
  isDictionaryLanguage,
  loadDictionaryLanguagePreference,
  saveDictionaryLanguagePreference,
} from "./lib/dictionaryLanguage";
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

const acceptedTypes = ".jpg,.jpeg,.png,.webp";

type LocalErrorCode = "one_image_only" | "select_image_first" | "unexpected_processing";
type DisplayError =
  | { readonly kind: "local"; readonly code: LocalErrorCode }
  | { readonly kind: "api"; readonly code: string };
type LanguageMutation = "study" | "dictionary" | null;

function requestedDictionaryLanguageOf(page: StudyPage): DictionaryLanguage {
  return page.requestedDictionaryLanguage ?? "en";
}

export function App() {
  const [file, setFile] = useState<File | null>(null);
  const [phase, setPhase] = useState<"idle" | "uploading" | "processing" | "complete" | "error">("idle");
  const [jobStatus, setJobStatus] = useState<JobStatus | null>(null);
  const [page, setPage] = useState<StudyPage | null>(null);
  const [pageAccess, setPageAccess] = useState<UploadData | null>(null);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [error, setError] = useState<DisplayError | null>(null);
  const [uiLocale, setUiLocale] = useState<UiLocale>(() => loadUiLocalePreference());
  const [preferredStudyLanguage, setPreferredStudyLanguage] = useState<StudyLanguage>(() =>
    loadStudyLanguagePreference(),
  );
  const [preferredDictionaryLanguage, setPreferredDictionaryLanguage] = useState<DictionaryLanguage>(() =>
    loadDictionaryLanguagePreference(),
  );
  const [languageMutation, setLanguageMutation] = useState<LanguageMutation>(null);
  const [studyLanguageErrorCode, setStudyLanguageErrorCode] = useState<string | null>(null);
  const [dictionaryLanguageErrorCode, setDictionaryLanguageErrorCode] = useState<string | null>(null);
  const operation = useRef<AbortController | null>(null);
  const messages = messagesFor(uiLocale);

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
    setFile(null);
    setPhase("idle");
    setJobStatus(null);
    setPage(null);
    setPageAccess(null);
    setImageUrl(null);
    setError(null);
    setLanguageMutation(null);
    setStudyLanguageErrorCode(null);
    setDictionaryLanguageErrorCode(null);
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

  const selectFile = (candidate: File | null) => {
    setFile(candidate);
    setError(null);
    if (phase === "error") setPhase("idle");
  };

  const handleDrop = (event: React.DragEvent<HTMLLabelElement>) => {
    event.preventDefault();
    const dropped = Array.from(event.dataTransfer.files);
    if (dropped.length !== 1) {
      setFile(null);
      setError({ kind: "local", code: "one_image_only" });
      setPhase("error");
      return;
    }
    selectFile(dropped[0] ?? null);
  };

  const runDictionaryReprojection = async (
    currentPage: StudyPage,
    access: UploadData,
    target: DictionaryLanguage,
    controller: AbortController,
  ) => {
    const previousLanguage = requestedDictionaryLanguageOf(currentPage);
    setPreferredDictionaryLanguage(target);
    saveDictionaryLanguagePreference(target);
    setDictionaryLanguageErrorCode(null);
    if (target === previousLanguage) return;

    setLanguageMutation("dictionary");
    try {
      await reprocessDictionaryLanguage(access, target, controller.signal);
      const result = await waitForPage(access, controller.signal, setJobStatus);
      setPage(result);
      const persistedLanguage = requestedDictionaryLanguageOf(result);
      setPreferredDictionaryLanguage(persistedLanguage);
      saveDictionaryLanguagePreference(persistedLanguage);
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") return;
      setPreferredDictionaryLanguage(previousLanguage);
      saveDictionaryLanguagePreference(previousLanguage);
      setDictionaryLanguageErrorCode(
        caught instanceof ApiError ? caught.code : "dictionary_language_update_failed",
      );
    } finally {
      if (operation.current === controller) operation.current = null;
      setLanguageMutation(null);
    }
  };

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!file) {
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
      setPhase("complete");

      const persistedDictionaryLanguage = requestedDictionaryLanguageOf(result);
      if (preferredDictionaryLanguage === persistedDictionaryLanguage) {
        saveDictionaryLanguagePreference(persistedDictionaryLanguage);
        operation.current = null;
        return;
      }
      await runDictionaryReprojection(result, upload, preferredDictionaryLanguage, controller);
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") return;
      setPageAccess(null);
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

  const changeDictionaryLanguage = async (target: DictionaryLanguage) => {
    if (!page || !pageAccess || languageMutation) return;
    const controller = new AbortController();
    operation.current = controller;
    await runDictionaryReprojection(page, pageAccess, target, controller);
  };

  const studyLanguageError = studyLanguageErrorCode
    ? studyLanguageErrorCode === "study_language_update_failed"
      ? messages.studyLanguageUpdateFailed
      : messages.apiError(studyLanguageErrorCode)
    : null;
  const dictionaryLanguageError = dictionaryLanguageErrorCode
    ? dictionaryLanguageErrorCode === "dictionary_language_update_failed"
      ? messages.dictionaryLanguageUpdateFailed
      : messages.apiError(dictionaryLanguageErrorCode)
    : null;

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
          dictionaryLanguageError={dictionaryLanguageError}
          onStudyLanguageChange={changeStudyLanguage}
          onDictionaryLanguageChange={changeDictionaryLanguage}
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
            <span className="file-detail">{file?.name ?? messages.fileDropHint}</span>
          </label>
          <input
            className="sr-only"
            id="page-image"
            name="page-image"
            type="file"
            accept={acceptedTypes}
            aria-label={messages.pageImageAria}
            aria-describedby="upload-retention"
            onChange={(event) => selectFile(event.target.files?.[0] ?? null)}
          />

          <button className="primary-button" type="submit" disabled={!file || phase === "uploading" || phase === "processing"}>
            {phase === "uploading" || phase === "processing" ? <LoaderCircle className="spinner" aria-hidden="true" /> : <ScanText aria-hidden="true" />}
            {phase === "uploading" ? messages.uploadingImage : phase === "processing" ? messages.jobStatus(jobStatus) : messages.analyzePage}
          </button>

          {phase === "uploading" || phase === "processing" ? (
            <button className="cancel-button" type="button" onClick={reset} aria-describedby="upload-retention">
              <X aria-hidden="true" /> {messages.stopFollowing}
            </button>
          ) : null}

          {error ? <p className="form-error" role="alert">{displayError(messages, error)}</p> : null}

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

function displayError(messages: UiMessages, error: DisplayError): string {
  if (error.kind === "api") return messages.apiError(error.code);
  if (error.code === "one_image_only") return messages.oneImageOnly;
  if (error.code === "select_image_first") return messages.selectImageFirst;
  return messages.unexpectedProcessingError;
}
