import {
  BookOpenText,
  ChevronDown,
  ChevronUp,
  FileImage,
  Minus,
  Plus,
  RotateCcw,
} from "lucide-react";
import type React from "react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";

import type { StudyLanguage, StudyPage, StudyRegion, StudyToken } from "../../lib/api";
import { studyLanguageLabel } from "../../lib/studyLanguage";
import { furiganaReading } from "./furigana";
import {
  DEFAULT_FURIGANA_MODE,
  readFuriganaPreference,
  writeFuriganaPreference,
  type FuriganaMode,
} from "./furiganaPreference";
import {
  chooseDefaultFitMode,
  constrainFitMode,
  hasHorizontalOverflow,
  readReaderViewportPreference,
  READER_ZOOM_MAX,
  READER_ZOOM_MIN,
  READER_ZOOM_STEP,
  writeReaderViewportPreference,
  type ReaderFitMode,
  type ReaderViewportPreference,
} from "./readerViewportPreference";

interface ReaderWorkspaceProps {
  readonly page: StudyPage;
  readonly imageUrl: string;
  readonly preferredStudyLanguage: StudyLanguage;
  readonly studyLanguageUpdating: boolean;
  readonly studyLanguageError: string | null;
  readonly onStudyLanguageChange: (language: StudyLanguage) => void;
  readonly onReset: () => void;
}

export function ReaderWorkspace({
  page,
  imageUrl,
  preferredStudyLanguage,
  studyLanguageUpdating,
  studyLanguageError,
  onStudyLanguageChange,
  onReset,
}: ReaderWorkspaceProps) {
  const [selectedId, setSelectedId] = useState<string | null>(page.regions[0]?.id ?? null);
  const [furiganaMode, setFuriganaMode] = useState<FuriganaMode>(() => readFuriganaPreference());
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const preferredFitMode = useRef<ReaderFitMode | null>(null);
  const [viewportPreference, setViewportPreference] = useState<ReaderViewportPreference>(() => {
    const stored = readReaderViewportPreference();
    preferredFitMode.current = stored?.fitMode ?? null;
    return stored ?? {
      fitMode: chooseDefaultFitMode(page.dimensions),
      zoom: 100,
    };
  });
  const selected =
    page.regions.find((region) => region.id === selectedId) ?? page.regions[0];
  const presentedFitMode = constrainFitMode(
    viewportPreference.fitMode,
    viewportPreference.zoom,
  );
  const [hasHorizontalPan, setHasHorizontalPan] = useState(false);

  useEffect(() => {
    if (page.regions.length === 0) {
      setSelectedId(null);
      return;
    }
    setSelectedId((current) =>
      current && page.regions.some((region) => region.id === current)
        ? current
        : page.regions[0].id,
    );
  }, [page.regions]);

  useEffect(() => {
    const requestedFitMode = preferredFitMode.current ?? viewportPreference.fitMode;
    const nextFitMode = constrainFitMode(requestedFitMode, viewportPreference.zoom);
    if (nextFitMode === viewportPreference.fitMode) {
      return;
    }
    setViewportPreference((current) => ({ ...current, fitMode: nextFitMode }));
  }, [viewportPreference.fitMode, viewportPreference.zoom]);

  useEffect(() => {
    writeReaderViewportPreference(viewportPreference);
  }, [viewportPreference]);

  useLayoutEffect(() => {
    const viewport = viewportRef.current;
    const canvas = canvasRef.current;
    if (!viewport || !canvas) {
      return;
    }

    const syncOverflow = () => {
      setHasHorizontalPan(
        hasHorizontalOverflow(viewport.clientWidth, canvas.getBoundingClientRect().width),
      );
    };
    syncOverflow();
    const ResizeObserverImpl = globalThis.ResizeObserver;
    if (!ResizeObserverImpl) {
      return;
    }
    const observer = new ResizeObserverImpl(syncOverflow);
    observer.observe(viewport);
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [page.dimensions, presentedFitMode, viewportPreference.zoom]);

  const changeFitMode = (fitMode: ReaderFitMode) => {
    preferredFitMode.current = fitMode;
    setViewportPreference((current) => ({
      ...current,
      fitMode: constrainFitMode(fitMode, current.zoom),
    }));
  };
  const changeZoom = (delta: number) => {
    setViewportPreference((current) => ({
      fitMode: constrainFitMode(
        preferredFitMode.current ?? current.fitMode,
        Math.min(READER_ZOOM_MAX, Math.max(READER_ZOOM_MIN, current.zoom + delta)),
      ),
      zoom: Math.min(READER_ZOOM_MAX, Math.max(READER_ZOOM_MIN, current.zoom + delta)),
    }));
  };

  return (
    <main className="reader-shell">
      <header className="reader-header">
        <div>
          <p className="eyebrow">MangaSensei</p>
          <h1>Leitor de estudo</h1>
        </div>
        <button className="button ghost" type="button" onClick={onReset}>
          <RotateCcw aria-hidden="true" />
          Nova página
        </button>
      </header>

      <section className="reader-language-bar" aria-label="Preferências de idioma">
        <label>
          <span>Idioma de estudo</span>
          <select
            aria-label="Idioma de estudo"
            value={preferredStudyLanguage}
            disabled={studyLanguageUpdating}
            onChange={(event) => onStudyLanguageChange(event.target.value as StudyLanguage)}
          >
            <option value="pt-BR">Português (Brasil)</option>
            <option value="en">English</option>
          </select>
        </label>
        <label>
          <span>Exibição de furigana</span>
          <select
            aria-label="Exibição de furigana"
            value={furiganaMode}
            onChange={(event) => {
              const next = event.target.value as FuriganaMode;
              setFuriganaMode(next);
              writeFuriganaPreference(next);
            }}
          >
            <option value="hiragana">Hiragana</option>
            <option value="katakana">Katakana</option>
            <option value="hidden">Ocultar</option>
          </select>
        </label>
        {studyLanguageUpdating ? <span>Atualizando idioma…</span> : null}
        {studyLanguageError ? <span role="alert">{studyLanguageError}</span> : null}
      </section>

      <section className="reader-page-section">
        <div className="reader-page-toolbar">
          <div className="reader-page-title">
            <FileImage aria-hidden="true" />
            <span>Página original</span>
          </div>
          <div className="reader-viewport-controls" aria-label="Controles de visualização">
            <button
              className="reader-fit-button"
              type="button"
              aria-pressed={presentedFitMode === "width"}
              onClick={() => changeFitMode("width")}
            >
              Ajustar largura
            </button>
            <button
              className="reader-fit-button"
              type="button"
              aria-pressed={presentedFitMode === "height"}
              onClick={() => changeFitMode("height")}
            >
              Ajustar altura
            </button>
            <button
              className="reader-zoom-button"
              type="button"
              aria-label="Diminuir zoom"
              disabled={viewportPreference.zoom <= READER_ZOOM_MIN}
              onClick={() => changeZoom(-READER_ZOOM_STEP)}
            >
              <Minus aria-hidden="true" />
            </button>
            <output aria-label="Nível de zoom">{viewportPreference.zoom}%</output>
            <button
              className="reader-zoom-button"
              type="button"
              aria-label="Aumentar zoom"
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
          aria-label={hasHorizontalPan ? "Visualização da página com rolagem horizontal" : undefined}
          tabIndex={hasHorizontalPan ? 0 : undefined}
          data-horizontal-pan={hasHorizontalPan}
        >
          <div
            ref={canvasRef}
            className="page-canvas"
            data-fit-mode={presentedFitMode}
            data-zoom={viewportPreference.zoom}
          >
            <img src={imageUrl} alt="Página original enviada para estudo" />
            <svg
              className="region-overlay"
              viewBox={`0 0 ${page.dimensions.width} ${page.dimensions.height}`}
              role="group"
              aria-label="Regiões de texto reconhecidas"
            >
              {page.regions.map((region, index) => (
                <RegionShape
                  key={region.id}
                  region={region}
                  index={index}
                  selected={region.id === selected?.id}
                  dimensions={page.dimensions}
                  onSelect={() => setSelectedId(region.id)}
                />
              ))}
            </svg>
          </div>
        </div>
        <p className="reader-meta">
          {page.regions.length} regiões · estudo {studyLanguageLabel(page.studyLanguage)} · OCR {page.ocr.detector}/{page.ocr.recognizer} · exclusão em 24 horas
        </p>
      </section>

      <StudyPanel
        region={selected}
        furiganaMode={furiganaMode}
        studyLanguage={page.studyLanguage}
        dictionaryLanguage={page.dictionaryLanguage}
      />
    </main>
  );
}

interface RegionShapeProps {
  readonly region: StudyRegion;
  readonly index: number;
  readonly selected: boolean;
  readonly dimensions: StudyPage["dimensions"];
  readonly onSelect: () => void;
}

function RegionShape({ region, index, selected, dimensions, onSelect }: RegionShapeProps) {
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
      aria-label={`Região ${index + 1}: ${region.text}`}
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
  dictionaryLanguage,
}: {
  readonly region: StudyRegion | undefined;
  readonly furiganaMode: FuriganaMode;
  readonly studyLanguage: StudyLanguage;
  readonly dictionaryLanguage: "en";
}) {
  if (!region) {
    return (
      <aside className="study-panel empty-study" aria-label="Painel de estudo">
        <BookOpenText aria-hidden="true" />
        <p>Nenhuma região de texto foi reconhecida nesta página.</p>
      </aside>
    );
  }

  return (
    <aside className="study-panel" aria-labelledby="study-title" aria-live="polite">
      <div className="study-heading">
        <span>#{region.readingOrder + 1}</span>
        <span>{Math.round(region.confidence * 100)}% de confiança</span>
      </div>
      <h2 id="study-title" lang="ja">
        {region.tokens.length > 0
          ? region.tokens.map((token, index) => (
            <RubyToken key={`${token.surface}-${index}`} token={token} furiganaMode={furiganaMode} />
          ))
          : region.text}
      </h2>

      <section aria-labelledby="translation-title">
        <p className="panel-label" id="translation-title">Tradução contextual</p>
        {region.translation ? (
          <p className="translation" lang={studyLanguage}>{region.translation}</p>
        ) : (
          <p className="translation">Análise contextual indisponível.</p>
        )}
        {region.explanation ? (
          <p className="explanation" lang={studyLanguage}>{region.explanation}</p>
        ) : null}
      </section>

      <section aria-labelledby="vocabulary-title">
        <p className="panel-label" id="vocabulary-title">
          Vocabulário <span className="panel-language-note">significados locais em inglês</span>
        </p>
        {region.vocabulary.length > 0 ? (
          <ul className="vocabulary-list">
            {region.vocabulary.map((item) => (
              <li key={`${item.id}|${item.lemma}|${item.reading}`}>
                <div>
                  <strong lang="ja">{item.lemma}</strong>
                  <span lang="ja">{item.reading}</span>
                </div>
                <p lang={dictionaryLanguage}>{item.meanings.join("; ")}</p>
                <small>{item.source}{item.jlpt ? ` · JLPT ${item.jlpt.level} não oficial` : ""}</small>
              </li>
            ))}
          </ul>
        ) : <p className="muted-panel">Nenhuma associação confiável ao dicionário.</p>}
      </section>

      <section aria-labelledby="grammar-title">
        <p className="panel-label" id="grammar-title">Gramática</p>
        {region.grammar.length > 0 ? (
          <ul className="grammar-list" lang={studyLanguage}>
            {region.grammar.map((point) => <li key={point}>{point}</li>)}
          </ul>
        ) : <p className="muted-panel">Nenhum ponto gramatical adicional.</p>}
      </section>
    </aside>
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
  if (!reading) {
    return <span>{token.surface}</span>;
  }
  return <ruby>{token.surface}<rp>（</rp><rt>{reading}</rt><rp>）</rp></ruby>;
}

function toSvgBox(
  normalized: StudyRegion["normalizedBbox"],
  dimensions: StudyPage["dimensions"],
) {
  return {
    x: normalized.x * dimensions.width,
    y: normalized.y * dimensions.height,
    width: normalized.width * dimensions.width,
    height: normalized.height * dimensions.height,
  };
}
