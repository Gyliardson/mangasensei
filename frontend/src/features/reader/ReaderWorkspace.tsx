import { BookOpenText, Minus, Plus, RotateCcw } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { StudyPage, StudyRegion, StudyToken } from "../../lib/api";
import {
  type StudyLanguage,
  isStudyLanguage,
  studyLanguageLabel,
} from "../../lib/studyLanguage";
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
    if (!canPanHorizontally) {
      viewport.scrollLeft = 0;
    }
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
            <p className="eyebrow">Página processada</p>
            <h1 id="reader-title">Selecione uma região</h1>
          </div>
          <div className="reader-header-actions">
            <div
              className="reader-study-actions"
              role="group"
              aria-label="Preferências de estudo"
              aria-busy={studyLanguageUpdating}
            >
              <label className="reader-preference">
                <span>Idioma de estudo</span>
                <select
                  aria-label="Idioma de estudo"
                  value={preferredStudyLanguage}
                  disabled={studyLanguageUpdating}
                  onChange={(event) => changeStudyLanguage(event.currentTarget.value)}
                >
                  <option value="pt-BR">Português (Brasil)</option>
                  <option value="en">Inglês</option>
                </select>
              </label>
              <label className="reader-preference">
                <span>Furigana na leitura</span>
                <select
                  aria-label="Exibição de furigana"
                  value={furiganaMode}
                  onChange={(event) => changeFuriganaMode(event.currentTarget.value)}
                >
                  <option value="hiragana">Hiragana</option>
                  <option value="katakana">Katakana</option>
                  <option value="hidden">Oculto</option>
                </select>
              </label>
            </div>
            <div className="reader-navigation" role="group" aria-label="Navegação">
              <button className="text-button" type="button" onClick={onReset}>
                <RotateCcw aria-hidden="true" /> Nova página
              </button>
            </div>
          </div>
        </div>

        {studyLanguageUpdating && preferredStudyLanguage !== page.studyLanguage ? (
          <p className="study-language-feedback" role="status">
            Atualizando explicações para {studyLanguageLabel(preferredStudyLanguage)}. O resultado
            exibido continua em {studyLanguageLabel(page.studyLanguage)} até a nova análise concluir.
          </p>
        ) : null}
        {studyLanguageError ? (
          <p className="study-language-feedback study-language-error" role="alert">
            {studyLanguageError} O resultado em {studyLanguageLabel(page.studyLanguage)} foi mantido.
          </p>
        ) : null}

        <div className="page-presentation-toolbar" role="group" aria-label="Apresentação da página">
          <label className="reader-preference page-fit-preference">
            <span>Ajuste da página</span>
            <select
              aria-label="Ajuste da página"
              value={presentedFitMode}
              onChange={(event) => changeFitMode(event.currentTarget.value)}
            >
              {mobileViewport ? (
                <>
                  <option value="width">Largura</option>
                  <option value="page">Página inteira</option>
                </>
              ) : (
                <>
                  <option value="comfortable">Confortável</option>
                  <option value="page">Página inteira</option>
                  <option value="width">Largura</option>
                </>
              )}
            </select>
          </label>
          <div className="reader-zoom" role="group" aria-label="Zoom da página">
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
              <li key={item.id}>
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
          <ul className="grammar-list">{region.grammar.map((point) => <li key={point}>{point}</li>)}</ul>
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
