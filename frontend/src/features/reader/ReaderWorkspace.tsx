import { BookOpenText, Minus, Plus, RotateCcw } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type {
  DictionarySourceReference,
  StudyPage,
  StudyRegion,
  StudyToken,
  VocabularyItem,
} from "../../lib/api";
import {
  type DictionaryLanguage,
  isDictionaryLanguage,
} from "../../lib/dictionaryLanguage";
import {
  type StudyLanguage,
  isStudyLanguage,
} from "../../lib/studyLanguage";
import { messagesFor, type UiMessages } from "../../lib/uiMessages";
import type { UiLocale } from "../../lib/uiLocale";
import { type FuriganaMode, furiganaReading, isFuriganaMode } from "./furigana";
import { loadFuriganaPreference, saveFuriganaPreference } from "./furiganaPreference";
import { toSvgBox } from "./overlay";
import {
  READER_ZOOM_MAX,
  READER_ZOOM_MIN,
  READER_ZOOM_STEP,
  type ReaderViewportMetrics,
  calculateReaderCanvasWidth,
  clampReaderZoom,
  effectiveReaderFitMode,
  isMobileReaderViewport,
  isReaderFitMode,
} from "./readerViewport";
import {
  type ReaderViewportPreference,
  loadReaderViewportPreference,
  saveReaderViewportPreference,
} from "./readerViewportPreference";
import "./reader-preferences.css";

interface ReaderWorkspaceProps {
  readonly page: StudyPage;
  readonly imageUrl: string;
  readonly uiLocale: UiLocale;
  readonly preferredStudyLanguage: StudyLanguage;
  readonly preferredDictionaryLanguage: DictionaryLanguage;
  readonly languageMutation: "study" | "dictionary" | null;
  readonly studyLanguageError: string | null;
  readonly dictionaryLanguageError: string | null;
  readonly onStudyLanguageChange: (language: StudyLanguage) => void;
  readonly onDictionaryLanguageChange: (language: DictionaryLanguage) => void;
  readonly onReset: () => void;
}

export function ReaderWorkspace({
  page,
  imageUrl,
  uiLocale,
  preferredStudyLanguage,
  preferredDictionaryLanguage,
  languageMutation,
  studyLanguageError,
  dictionaryLanguageError,
  onStudyLanguageChange,
  onDictionaryLanguageChange,
  onReset,
}: ReaderWorkspaceProps) {
  const [selectedId, setSelectedId] = useState(page.regions[0]?.id ?? null);
  const [furiganaMode, setFuriganaMode] = useState<FuriganaMode>(() => loadFuriganaPreference());
  const [viewportPreference, setViewportPreference] = useState<ReaderViewportPreference>(() =>
    loadReaderViewportPreference(),
  );
  const [viewportMetrics, setViewportMetrics] = useState<ReaderViewportMetrics | null>(null);
  const [hasHorizontalPan, setHasHorizontalPan] = useState(false);
  const viewportRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLDivElement>(null);
  const selected = page.regions.find((region) => region.id === selectedId) ?? page.regions[0];
  const readerWidth = viewportMetrics?.width ?? window.innerWidth;
  const mobileViewport = isMobileReaderViewport(readerWidth);
  const presentedFitMode = effectiveReaderFitMode(viewportPreference.fitMode, readerWidth);
  const messages = messagesFor(uiLocale);
  const persistedDictionaryLanguage = page.requestedDictionaryLanguage ?? "en";
  const languageUpdating = languageMutation !== null;

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;

    const measure = () => {
      const rect = viewport.getBoundingClientRect();
      const width = Math.max(1, viewport.clientWidth || rect.width || window.innerWidth);
      const height = Math.max(240, window.innerHeight - Math.max(rect.top, 0) - 24);
      setViewportMetrics((current) => {
        if (
          current
          && Math.abs(current.width - width) < 1
          && Math.abs(current.height - height) < 1
        ) {
          return current;
        }
        return { width, height };
      });
    };

    measure();
    window.addEventListener("resize", measure);
    const resizeObserver = typeof ResizeObserver === "undefined"
      ? null
      : new ResizeObserver(() => measure());
    resizeObserver?.observe(viewport);

    return () => {
      window.removeEventListener("resize", measure);
      resizeObserver?.disconnect();
    };
  }, []);

  useEffect(() => {
    const viewport = viewportRef.current;
    const canvas = canvasRef.current;
    if (!viewport || !canvas || !viewportMetrics) return;

    const canvasWidth = calculateReaderCanvasWidth(
      page.dimensions,
      viewportMetrics,
      viewportPreference.fitMode,
      viewportPreference.zoom,
    );
    canvas.style.width = `${Math.round(canvasWidth)}px`;
    const canPanHorizontally = canvasWidth > viewportMetrics.width + 1;
    setHasHorizontalPan(canPanHorizontally);
    if (!canPanHorizontally) viewport.scrollLeft = 0;
  }, [page.dimensions, viewportMetrics, viewportPreference.fitMode, viewportPreference.zoom]);

  const changeFuriganaMode = (value: string) => {
    if (!isFuriganaMode(value)) return;
    setFuriganaMode(value);
    saveFuriganaPreference(value);
  };

  const changeStudyLanguage = (value: string) => {
    if (!isStudyLanguage(value)) return;
    onStudyLanguageChange(value);
  };

  const changeDictionaryLanguage = (value: string) => {
    if (!isDictionaryLanguage(value)) return;
    onDictionaryLanguageChange(value);
  };

  const changeFitMode = (value: string) => {
    if (!isReaderFitMode(value)) return;
    setViewportPreference((current) => {
      const next = { ...current, fitMode: value };
      saveReaderViewportPreference(next);
      return next;
    });
  };

  const changeZoom = (delta: number) => {
    setViewportPreference((current) => {
      const next = { ...current, zoom: clampReaderZoom(current.zoom + delta) };
      saveReaderViewportPreference(next);
      return next;
    });
  };

  return (
    <main id="conteudo" className="reader-layout">
      <section className="page-stage" aria-labelledby="reader-title">
        <div className="reader-toolbar">
          <div>
            <p className="eyebrow">{messages.processedPage}</p>
            <h1 id="reader-title">{messages.selectRegion}</h1>
          </div>
          <div className="reader-header-actions">
            <div
              className="reader-study-actions"
              role="group"
              aria-label={messages.studyPreferences}
              aria-busy={languageUpdating}
            >
              <label className="reader-preference">
                <span>{messages.studyLanguageLabel}</span>
                <select
                  aria-label={messages.studyLanguageLabel}
                  value={preferredStudyLanguage}
                  disabled={languageUpdating}
                  onChange={(event) => changeStudyLanguage(event.currentTarget.value)}
                >
                  <option value="pt-BR">{messages.studyLanguageName("pt-BR")}</option>
                  <option value="en">{messages.studyLanguageName("en")}</option>
                </select>
              </label>
              <label className="reader-preference">
                <span>{messages.dictionaryLanguageLabel}</span>
                <select
                  aria-label={messages.dictionaryLanguageLabel}
                  value={preferredDictionaryLanguage}
                  disabled={languageUpdating}
                  onChange={(event) => changeDictionaryLanguage(event.currentTarget.value)}
                >
                  <option value="en">{messages.dictionaryLanguageName("en")}</option>
                  <option value="de">{messages.dictionaryLanguageName("de")}</option>
                  <option value="pt-BR">{messages.dictionaryLanguageName("pt-BR")}</option>
                </select>
              </label>
              <label className="reader-preference">
                <span>{messages.furiganaReading}</span>
                <select
                  aria-label={messages.furiganaReading}
                  value={furiganaMode}
                  onChange={(event) => changeFuriganaMode(event.currentTarget.value)}
                >
                  <option value="hiragana">{messages.furiganaHiragana}</option>
                  <option value="katakana">{messages.furiganaKatakana}</option>
                  <option value="hidden">{messages.furiganaHidden}</option>
                </select>
              </label>
            </div>
            <div className="reader-navigation" role="group" aria-label={messages.navigation}>
              <button className="text-button" type="button" onClick={onReset}>
                <RotateCcw aria-hidden="true" /> {messages.newPage}
              </button>
            </div>
          </div>
        </div>

        {languageMutation === "study" && preferredStudyLanguage !== page.studyLanguage ? (
          <p className="study-language-feedback" role="status">
            {messages.updatingStudyLanguage(
              messages.studyLanguageName(preferredStudyLanguage),
              messages.studyLanguageName(page.studyLanguage),
            )}
          </p>
        ) : null}
        {languageMutation === "dictionary" && preferredDictionaryLanguage !== persistedDictionaryLanguage ? (
          <p className="study-language-feedback" role="status">
            {messages.updatingDictionaryLanguage(
              messages.dictionaryLanguageName(preferredDictionaryLanguage),
              messages.dictionaryLanguageName(persistedDictionaryLanguage),
            )}
          </p>
        ) : null}
        {studyLanguageError ? (
          <p className="study-language-feedback study-language-error" role="alert">
            {studyLanguageError} {messages.retainedStudyLanguage(messages.studyLanguageName(page.studyLanguage))}
          </p>
        ) : null}
        {dictionaryLanguageError ? (
          <p className="study-language-feedback study-language-error" role="alert">
            {dictionaryLanguageError}{" "}
            {messages.retainedDictionaryLanguage(messages.dictionaryLanguageName(persistedDictionaryLanguage))}
          </p>
        ) : null}

        <div className="page-presentation-toolbar" role="group" aria-label={messages.pagePresentation}>
          <label className="reader-preference page-fit-preference">
            <span>{messages.pageFit}</span>
            <select
              aria-label={messages.pageFit}
              value={presentedFitMode}
              onChange={(event) => changeFitMode(event.currentTarget.value)}
            >
              {mobileViewport ? (
                <>
                  <option value="width">{messages.fitWidth}</option>
                  <option value="page">{messages.fitPage}</option>
                </>
              ) : (
                <>
                  <option value="comfortable">{messages.fitComfortable}</option>
                  <option value="page">{messages.fitPage}</option>
                  <option value="width">{messages.fitWidth}</option>
                </>
              )}
            </select>
          </label>
          <div className="reader-zoom" role="group" aria-label={messages.pageZoom}>
            <button
              className="reader-zoom-button"
              type="button"
              aria-label={messages.decreaseZoom}
              disabled={viewportPreference.zoom <= READER_ZOOM_MIN}
              onClick={() => changeZoom(-READER_ZOOM_STEP)}
            >
              <Minus aria-hidden="true" />
            </button>
            <output aria-label={messages.zoomLevel}>{viewportPreference.zoom}%</output>
            <button
              className="reader-zoom-button"
              type="button"
              aria-label={messages.increaseZoom}
              disabled={viewportPreference.zoom >= READER_ZOOM_MAX}
              onClick={() => changeZoom(READER_ZOOM_STEP)}
            >
              <Plus aria-hidden="true" />
            </button>
          </div>
        </div>

        <div
          ref={viewportRef}
          className="page-viewport"
          role={hasHorizontalPan ? "region" : undefined}
          aria-label={hasHorizontalPan ? messages.horizontalPan : undefined}
          tabIndex={hasHorizontalPan ? 0 : undefined}
          data-horizontal-pan={hasHorizontalPan}
        >
          <div
            ref={canvasRef}
            className="page-canvas"
            data-fit-mode={presentedFitMode}
            data-zoom={viewportPreference.zoom}
          >
            <img src={imageUrl} alt={messages.originalPageAlt} />
            <svg
              className="region-overlay"
              viewBox={`0 0 ${page.dimensions.width} ${page.dimensions.height}`}
              role="group"
              aria-label={messages.recognizedRegions}
            >
              {page.regions.map((region, index) => (
                <RegionShape
                  key={region.id}
                  region={region}
                  index={index}
                  selected={region.id === selected?.id}
                  dimensions={page.dimensions}
                  messages={messages}
                  onSelect={() => setSelectedId(region.id)}
                />
              ))}
            </svg>
          </div>
        </div>
        <p className="reader-meta">
          {messages.readerMeta(
            page.regions.length,
            messages.studyLanguageName(page.studyLanguage),
            page.ocr.detector,
            page.ocr.recognizer,
          )}
        </p>
      </section>

      <StudyPanel
        region={selected}
        furiganaMode={furiganaMode}
        studyLanguage={page.studyLanguage}
        requestedDictionaryLanguage={persistedDictionaryLanguage}
        dictionarySources={page.dictionarySources ?? []}
        messages={messages}
      />
    </main>
  );
}

interface RegionShapeProps {
  readonly region: StudyRegion;
  readonly index: number;
  readonly selected: boolean;
  readonly dimensions: StudyPage["dimensions"];
  readonly messages: UiMessages;
  readonly onSelect: () => void;
}

function RegionShape({ region, index, selected, dimensions, messages, onSelect }: RegionShapeProps) {
  const box = toSvgBox(region.normalizedBbox, dimensions);
  const activate = (event: React.KeyboardEvent<SVGGElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelect();
    }
  };

  return (
    <g
      className="region-shape"
      data-selected={selected}
      role="button"
      tabIndex={0}
      aria-label={messages.regionAria(index + 1, region.text)}
      aria-pressed={selected}
      onClick={onSelect}
      onKeyDown={activate}
    >
      <rect x={box.x} y={box.y} width={box.width} height={box.height} rx="4" />
      <text x={box.x + 8} y={box.y + 18} aria-hidden="true">
        {index + 1}
      </text>
    </g>
  );
}

function StudyPanel({
  region,
  furiganaMode,
  studyLanguage,
  requestedDictionaryLanguage,
  dictionarySources,
  messages,
}: {
  readonly region: StudyRegion | undefined;
  readonly furiganaMode: FuriganaMode;
  readonly studyLanguage: StudyLanguage;
  readonly requestedDictionaryLanguage: DictionaryLanguage;
  readonly dictionarySources: readonly DictionarySourceReference[];
  readonly messages: UiMessages;
}) {
  if (!region) {
    return (
      <aside className="study-panel empty-study" aria-label={messages.studyPanel}>
        <BookOpenText aria-hidden="true" />
        <p>{messages.noRecognizedRegions}</p>
      </aside>
    );
  }

  return (
    <aside className="study-panel" aria-labelledby="study-title" aria-live="polite">
      <div className="study-heading">
        <span>#{region.readingOrder + 1}</span>
        <span>{messages.confidence(Math.round(region.confidence * 100))}</span>
      </div>
      <h2 id="study-title" lang="ja">
        {region.tokens.length > 0
          ? region.tokens.map((token, index) => (
            <RubyToken key={`${token.surface}-${index}`} token={token} furiganaMode={furiganaMode} />
          ))
          : region.text}
      </h2>

      <section aria-labelledby="translation-title">
        <p className="panel-label" id="translation-title">{messages.contextualTranslation}</p>
        {region.translation ? (
          <p className="translation" lang={studyLanguage}>{region.translation}</p>
        ) : (
          <p className="translation">{messages.contextualUnavailable}</p>
        )}
        {region.explanation ? (
          <p className="explanation" lang={studyLanguage}>{region.explanation}</p>
        ) : null}
      </section>

      <section aria-labelledby="vocabulary-title">
        <p className="panel-label" id="vocabulary-title">{messages.vocabulary}</p>
        <p className="dictionary-requested-note">
          {messages.dictionaryRequestedNote(messages.dictionaryLanguageName(requestedDictionaryLanguage))}
        </p>
        {requestedDictionaryLanguage === "pt-BR" ? (
          <p className="dictionary-fallback-notice" role="note">
            {messages.unsupportedPortugueseDictionary}
          </p>
        ) : null}
        {region.vocabulary.length > 0 ? (
          <ul className="vocabulary-list">
            {region.vocabulary.map((item) => (
              <VocabularyCard
                key={`${item.id}|${item.lemma}|${item.reading}`}
                item={item}
                dictionarySources={dictionarySources}
                messages={messages}
              />
            ))}
          </ul>
        ) : <p className="muted-panel">{messages.noDictionaryMatch}</p>}
      </section>

      <section aria-labelledby="grammar-title">
        <p className="panel-label" id="grammar-title">{messages.grammar}</p>
        {region.grammar.length > 0 ? (
          <ul className="grammar-list" lang={studyLanguage}>
            {region.grammar.map((point) => <li key={point}>{point}</li>)}
          </ul>
        ) : <p className="muted-panel">{messages.noGrammarPoint}</p>}
      </section>
    </aside>
  );
}

function VocabularyCard({
  item,
  dictionarySources,
  messages,
}: {
  readonly item: VocabularyItem;
  readonly dictionarySources: readonly DictionarySourceReference[];
  readonly messages: UiMessages;
}) {
  const effectiveLanguage = item.effectiveLanguage ?? "en";
  const fallbackUsed = item.fallbackUsed ?? false;
  const source = item.sourceRef
    ? dictionarySources.find((candidate) => candidate.ref === item.sourceRef)
    : undefined;

  return (
    <li>
      <div>
        <strong lang="ja">{item.lemma}</strong>
        <span lang="ja">{item.reading}</span>
      </div>
      <p lang={effectiveLanguage}>{item.meanings.join("; ")}</p>
      {fallbackUsed ? <span className="dictionary-fallback-badge">{messages.englishFallbackBadge}</span> : null}
      <small>
        {source
          ? messages.dictionaryProvenance(
            source.dataset,
            messages.dictionaryLanguageName(source.productLanguage),
          )
          : item.source}
        {item.jlpt ? ` · ${messages.unofficialJlpt(item.jlpt.level)}` : ""}
      </small>
    </li>
  );
}

function RubyToken({
  token,
  furiganaMode,
}: {
  readonly token: StudyToken;
  readonly furiganaMode: FuriganaMode;
}) {
  const reading = furiganaReading(token.surface, token.reading, furiganaMode);
  if (!reading) return <span>{token.surface}</span>;
  return <ruby>{token.surface}<rp>（</rp><rt>{reading}</rt><rp>）</rp></ruby>;
}
