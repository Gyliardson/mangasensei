import { ChevronLeft, ChevronRight } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  type DocumentSnapshot,
  type DocumentUploadData,
  type JobStatus,
  type StudyPage,
  fetchDocumentPage,
  fetchDocumentProtectedImage,
  fetchDocumentSnapshot,
  reprocessDocumentDictionaryLanguage,
  reprocessDocumentStudyLanguage,
} from "../../lib/api";
import {
  type DictionaryLanguage,
  saveDictionaryLanguagePreference,
} from "../../lib/dictionaryLanguage";
import {
  documentNeedsPolling,
  waitForDocumentPageResult,
} from "../../lib/documentPolling";
import { documentMessagesFor } from "../../lib/documentUiMessages";
import {
  type StudyLanguage,
  saveStudyLanguagePreference,
} from "../../lib/studyLanguage";
import { messagesFor } from "../../lib/uiMessages";
import type { UiLocale } from "../../lib/uiLocale";
import { ReaderWorkspace } from "./ReaderWorkspace";
import "./document-reader.css";

interface DocumentReaderProps {
  readonly access: DocumentUploadData;
  readonly uiLocale: UiLocale;
  readonly preferredStudyLanguage: StudyLanguage;
  readonly preferredDictionaryLanguage: DictionaryLanguage;
  readonly onPreferredStudyLanguageChange: (language: StudyLanguage) => void;
  readonly onPreferredDictionaryLanguageChange: (language: DictionaryLanguage) => void;
  readonly onReset: () => void;
}

type LanguageMutation = "study" | "dictionary" | null;

function requestedDictionaryLanguageOf(page: StudyPage): DictionaryLanguage {
  return page.requestedDictionaryLanguage ?? "en";
}

function isFailedStatus(status: JobStatus | undefined): boolean {
  return status === "failed" || status === "expired";
}

export function DocumentReader({
  access,
  uiLocale,
  preferredStudyLanguage,
  preferredDictionaryLanguage,
  onPreferredStudyLanguageChange,
  onPreferredDictionaryLanguageChange,
  onReset,
}: DocumentReaderProps) {
  const [snapshot, setSnapshot] = useState<DocumentSnapshot>(access);
  const [currentPageId, setCurrentPageId] = useState(access.pages[0]?.pageId ?? "");
  const [page, setPage] = useState<StudyPage | null>(null);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [languageMutation, setLanguageMutation] = useState<LanguageMutation>(null);
  const [studyLanguageErrorCode, setStudyLanguageErrorCode] = useState<string | null>(null);
  const [dictionaryLanguageErrorCode, setDictionaryLanguageErrorCode] = useState<string | null>(null);
  const pageRequest = useRef<AbortController | null>(null);
  const mutationRequest = useRef<AbortController | null>(null);
  const requestGeneration = useRef(0);
  const autoDictionaryAttempt = useRef<string | null>(null);
  const currentPageIdRef = useRef(currentPageId);
  const preferredDictionaryLanguageRef = useRef(preferredDictionaryLanguage);
  const imageUrlRef = useRef<string | null>(null);
  const messages = messagesFor(uiLocale);
  const documentMessages = documentMessagesFor(uiLocale);

  currentPageIdRef.current = currentPageId;
  preferredDictionaryLanguageRef.current = preferredDictionaryLanguage;

  const rawCurrentIndex = snapshot.pages.findIndex(
    (summary) => summary.pageId === currentPageId,
  );
  const currentIndex = Math.max(0, rawCurrentIndex);
  const currentSummary = snapshot.pages[currentIndex];
  const currentResultAvailable = currentSummary?.resultAvailable ?? false;
  const shouldPollDocument = documentNeedsPolling(snapshot);

  const replaceImageUrl = useCallback((next: string | null) => {
    if (imageUrlRef.current && imageUrlRef.current !== next) {
      URL.revokeObjectURL(imageUrlRef.current);
    }
    imageUrlRef.current = next;
    setImageUrl(next);
  }, []);

  useEffect(() => () => {
    pageRequest.current?.abort();
    mutationRequest.current?.abort();
    if (imageUrlRef.current) {
      URL.revokeObjectURL(imageUrlRef.current);
      imageUrlRef.current = null;
    }
  }, []);

  useEffect(() => {
    if (!shouldPollDocument || languageMutation !== null) return;
    const controller = new AbortController();
    let timer = 0;

    const poll = async () => {
      try {
        const next = await fetchDocumentSnapshot(access, controller.signal);
        setSnapshot(next);
        if (documentNeedsPolling(next)) timer = window.setTimeout(poll, 1_000);
      } catch (caught) {
        if (!(caught instanceof DOMException && caught.name === "AbortError")) {
          timer = window.setTimeout(poll, 2_000);
        }
      }
    };

    timer = window.setTimeout(poll, 700);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [access, languageMutation, shouldPollDocument]);

  useEffect(() => {
    pageRequest.current?.abort();
    mutationRequest.current?.abort();
    mutationRequest.current = null;
    setLanguageMutation(null);
    setStudyLanguageErrorCode(null);
    setDictionaryLanguageErrorCode(null);
    setPage(null);
    replaceImageUrl(null);
    autoDictionaryAttempt.current = null;

    if (!currentResultAvailable) return;

    const controller = new AbortController();
    pageRequest.current = controller;
    const generation = ++requestGeneration.current;

    void (async () => {
      let protectedImage: string | null = null;
      try {
        const [loadedPage, loadedImage] = await Promise.all([
          fetchDocumentPage(access, currentPageId, controller.signal),
          fetchDocumentProtectedImage(access, currentPageId, controller.signal),
        ]);
        protectedImage = loadedImage;
        if (generation !== requestGeneration.current || currentPageIdRef.current !== currentPageId) {
          URL.revokeObjectURL(loadedImage);
          return;
        }
        setPage(loadedPage);
        replaceImageUrl(loadedImage);
        protectedImage = null;

        const targetDictionaryLanguage = preferredDictionaryLanguageRef.current;
        const persistedDictionaryLanguage = requestedDictionaryLanguageOf(loadedPage);
        if (persistedDictionaryLanguage !== targetDictionaryLanguage) {
          const attemptKey = `${currentPageId}:${targetDictionaryLanguage}`;
          if (autoDictionaryAttempt.current !== attemptKey) {
            autoDictionaryAttempt.current = attemptKey;
            const mutation = new AbortController();
            mutationRequest.current = mutation;
            setLanguageMutation("dictionary");
            try {
              await reprocessDocumentDictionaryLanguage(
                access,
                currentPageId,
                targetDictionaryLanguage,
                mutation.signal,
              );
              const refreshed = await waitForDocumentPageResult(
                access,
                currentPageId,
                mutation.signal,
                setSnapshot,
                (candidate) =>
                  requestedDictionaryLanguageOf(candidate) === targetDictionaryLanguage,
              );
              if (currentPageIdRef.current === currentPageId) setPage(refreshed);
            } catch (caught) {
              if (!(caught instanceof DOMException && caught.name === "AbortError")) {
                setDictionaryLanguageErrorCode(
                  caught instanceof ApiError ? caught.code : "dictionary_language_update_failed",
                );
              }
            } finally {
              if (mutationRequest.current === mutation) {
                mutationRequest.current = null;
                setLanguageMutation(null);
              }
            }
          }
        }
      } catch (caught) {
        if (protectedImage) URL.revokeObjectURL(protectedImage);
        if (!(caught instanceof DOMException && caught.name === "AbortError")) {
          setStudyLanguageErrorCode(caught instanceof ApiError ? caught.code : "request_failed");
        }
      } finally {
        if (pageRequest.current === controller) pageRequest.current = null;
      }
    })();

    return () => controller.abort();
  }, [access, currentPageId, currentResultAvailable, replaceImageUrl]);

  const selectPage = (pageId: string) => {
    if (pageId === currentPageId) return;
    requestGeneration.current += 1;
    setCurrentPageId(pageId);
  };

  const changeStudyLanguage = async (target: StudyLanguage) => {
    if (!page || languageMutation) return;
    const previousPreference = preferredStudyLanguage;
    onPreferredStudyLanguageChange(target);
    saveStudyLanguagePreference(target);
    setStudyLanguageErrorCode(null);
    if (target === page.studyLanguage) return;

    const controller = new AbortController();
    mutationRequest.current?.abort();
    mutationRequest.current = controller;
    setLanguageMutation("study");
    try {
      await reprocessDocumentStudyLanguage(access, page.pageId, target, controller.signal);
      const refreshed = await waitForDocumentPageResult(
        access,
        page.pageId,
        controller.signal,
        setSnapshot,
        (candidate) => candidate.studyLanguage === target,
      );
      if (currentPageIdRef.current === page.pageId) setPage(refreshed);
      onPreferredStudyLanguageChange(target);
      saveStudyLanguagePreference(target);
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") return;
      onPreferredStudyLanguageChange(previousPreference);
      saveStudyLanguagePreference(previousPreference);
      setStudyLanguageErrorCode(
        caught instanceof ApiError ? caught.code : "study_language_update_failed",
      );
    } finally {
      if (mutationRequest.current === controller) {
        mutationRequest.current = null;
        setLanguageMutation(null);
      }
    }
  };

  const changeDictionaryLanguage = async (target: DictionaryLanguage) => {
    if (!page || languageMutation) return;
    const previousPreference = preferredDictionaryLanguage;
    onPreferredDictionaryLanguageChange(target);
    saveDictionaryLanguagePreference(target);
    setDictionaryLanguageErrorCode(null);
    if (target === requestedDictionaryLanguageOf(page)) return;

    const controller = new AbortController();
    mutationRequest.current?.abort();
    mutationRequest.current = controller;
    setLanguageMutation("dictionary");
    try {
      await reprocessDocumentDictionaryLanguage(access, page.pageId, target, controller.signal);
      const refreshed = await waitForDocumentPageResult(
        access,
        page.pageId,
        controller.signal,
        setSnapshot,
        (candidate) => requestedDictionaryLanguageOf(candidate) === target,
      );
      if (currentPageIdRef.current === page.pageId) setPage(refreshed);
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") return;
      onPreferredDictionaryLanguageChange(previousPreference);
      saveDictionaryLanguagePreference(previousPreference);
      setDictionaryLanguageErrorCode(
        caught instanceof ApiError ? caught.code : "dictionary_language_update_failed",
      );
    } finally {
      if (mutationRequest.current === controller) {
        mutationRequest.current = null;
        setLanguageMutation(null);
      }
    }
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

  const navigation = (
    <section className="document-navigation-shell" aria-label={documentMessages.documentNavigation}>
      <div className="document-progress" role="status">
        <strong>{documentMessages.pageOf(currentIndex + 1, snapshot.progress.totalPages)}</strong>
        <span>
          {documentMessages.documentProgress(
            snapshot.progress.completedPages,
            snapshot.progress.totalPages,
            snapshot.progress.processingPages,
            snapshot.progress.failedPages,
          )}
        </span>
      </div>
      <div className="document-prev-next" role="group" aria-label={documentMessages.documentNavigation}>
        <button
          type="button"
          className="text-button"
          disabled={currentIndex <= 0}
          onClick={() => selectPage(snapshot.pages[currentIndex - 1]?.pageId ?? currentPageId)}
        >
          <ChevronLeft aria-hidden="true" /> {documentMessages.previousPage}
        </button>
        <button
          type="button"
          className="text-button"
          disabled={currentIndex >= snapshot.pages.length - 1}
          onClick={() => selectPage(snapshot.pages[currentIndex + 1]?.pageId ?? currentPageId)}
        >
          {documentMessages.nextPage} <ChevronRight aria-hidden="true" />
        </button>
      </div>
      <ol className="document-page-index">
        {snapshot.pages.map((summary) => {
          const failed = isFailedStatus(summary.status);
          return (
            <li key={summary.pageId}>
              <button
                type="button"
                aria-current={summary.pageId === currentPageId ? "page" : undefined}
                aria-label={documentMessages.pageStatus(
                  summary.ordinal + 1,
                  summary.status,
                  summary.resultAvailable,
                )}
                data-page-status={summary.resultAvailable ? "readable" : failed ? "failed" : "processing"}
                onClick={() => selectPage(summary.pageId)}
              >
                <span>{summary.ordinal + 1}</span>
                <small>
                  {summary.resultAvailable
                    ? documentMessages.readablePage
                    : failed
                      ? documentMessages.failedPage
                      : documentMessages.processingPage}
                </small>
              </button>
            </li>
          );
        })}
      </ol>
      <p className="document-session-note">{documentMessages.documentReloadNote}</p>
    </section>
  );

  if (!page || !imageUrl || !currentSummary?.resultAvailable) {
    const currentFailed = isFailedStatus(currentSummary?.status);
    return (
      <main id="conteudo" className="document-reader-loading">
        {navigation}
        <p role={currentFailed ? "alert" : "status"}>
          {currentFailed ? documentMessages.failedPage : documentMessages.processingPage}
        </p>
      </main>
    );
  }

  return (
    <>
      {navigation}
      <ReaderWorkspace
        page={page}
        imageUrl={imageUrl}
        uiLocale={uiLocale}
        preferredStudyLanguage={preferredStudyLanguage}
        preferredDictionaryLanguage={preferredDictionaryLanguage}
        languageMutation={languageMutation}
        studyLanguageError={studyLanguageError}
        dictionaryLanguageError={dictionaryLanguageError}
        onStudyLanguageChange={(language) => void changeStudyLanguage(language)}
        onDictionaryLanguageChange={(language) => void changeDictionaryLanguage(language)}
        onReset={onReset}
      />
    </>
  );
}
