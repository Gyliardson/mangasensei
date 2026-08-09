import { BookOpenText, RotateCcw } from "lucide-react";
import { useState } from "react";

import type { StudyPage, StudyRegion, StudyToken } from "../../lib/api";
import { furiganaReading } from "./furigana";
import { toSvgBox } from "./overlay";

interface ReaderWorkspaceProps {
  readonly page: StudyPage;
  readonly imageUrl: string;
  readonly onReset: () => void;
}

export function ReaderWorkspace({ page, imageUrl, onReset }: ReaderWorkspaceProps) {
  const [selectedId, setSelectedId] = useState(page.regions[0]?.id ?? null);
  const selected = page.regions.find((region) => region.id === selectedId) ?? page.regions[0];

  return (
    <main id="conteudo" className="reader-layout">
      <section className="page-stage" aria-labelledby="reader-title">
        <div className="reader-toolbar">
          <div>
            <p className="eyebrow">Página processada</p>
            <h1 id="reader-title">Selecione uma região</h1>
          </div>
          <button className="text-button" type="button" onClick={onReset}>
            <RotateCcw aria-hidden="true" /> Nova página
          </button>
        </div>

        <div className="page-canvas">
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
        <p className="reader-meta">
          {page.regions.length} regiões · OCR {page.ocr.detector}/{page.ocr.recognizer} · exclusão em 24 horas
        </p>
      </section>

      <StudyPanel region={selected} />
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

function StudyPanel({ region }: { readonly region: StudyRegion | undefined }) {
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
        {region.tokens.length > 0 ? region.tokens.map((token, index) => <RubyToken key={`${token.surface}-${index}`} token={token} />) : region.text}
      </h2>

      <section aria-labelledby="translation-title">
        <p className="panel-label" id="translation-title">Tradução contextual</p>
        <p className="translation">{region.translation ?? "Análise contextual indisponível."}</p>
        {region.explanation ? <p className="explanation">{region.explanation}</p> : null}
      </section>

      <section aria-labelledby="vocabulary-title">
        <p className="panel-label" id="vocabulary-title">Vocabulário</p>
        {region.vocabulary.length > 0 ? (
          <ul className="vocabulary-list">
            {region.vocabulary.map((item) => (
              <li key={item.id}>
                <div>
                  <strong lang="ja">{item.lemma}</strong>
                  <span lang="ja">{item.reading}</span>
                </div>
                <p>{item.meanings.join("; ")}</p>
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

function RubyToken({ token }: { readonly token: StudyToken }) {
  const reading = furiganaReading(token.surface, token.reading);
  if (!reading) {
    return <span>{token.surface}</span>;
  }
  return <ruby>{token.surface}<rp>（</rp><rt>{reading}</rt><rp>）</rp></ruby>;
}
