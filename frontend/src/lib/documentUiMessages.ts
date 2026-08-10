import type { JobStatus } from "./api";
import type { UiLocale } from "./uiLocale";

export interface DocumentUiMessages {
  readonly selectedPages: (count: number) => string;
  readonly movePageUp: (name: string) => string;
  readonly movePageDown: (name: string) => string;
  readonly removePage: (name: string) => string;
  readonly clearPages: string;
  readonly pageOrderHint: string;
  readonly analyzePages: (count: number) => string;
  readonly pageOf: (current: number, total: number) => string;
  readonly previousPage: string;
  readonly nextPage: string;
  readonly documentNavigation: string;
  readonly documentProgress: (completed: number, total: number) => string;
  readonly processingPage: string;
  readonly failedPage: string;
  readonly readablePage: string;
  readonly pageStatus: (page: number, status: JobStatus, readable: boolean) => string;
  readonly documentUploadFailed: string;
  readonly documentPageLimit: string;
  readonly documentByteLimit: string;
  readonly documentPixelLimit: string;
  readonly documentReloadNote: string;
}

const en: DocumentUiMessages = {
  selectedPages: (count) => `${count} ${count === 1 ? "page" : "pages"} selected`,
  movePageUp: (name) => `Move ${name} earlier`,
  movePageDown: (name) => `Move ${name} later`,
  removePage: (name) => `Remove ${name}`,
  clearPages: "Clear selection",
  pageOrderHint: "The displayed order is the upload order. Use the controls to change it before analysis.",
  analyzePages: (count) => count <= 1 ? "Analyze page" : `Analyze ${count} pages`,
  pageOf: (current, total) => `Page ${current} of ${total}`,
  previousPage: "Previous",
  nextPage: "Next",
  documentNavigation: "Document pages",
  documentProgress: (completed, total) => `${completed} / ${total} pages complete`,
  processingPage: "Processing",
  failedPage: "Failed",
  readablePage: "Readable",
  pageStatus: (page, status, readable) => `Page ${page}: ${readable ? "readable" : status.replaceAll("_", " ")}`,
  documentUploadFailed: "The document could not be created.",
  documentPageLimit: "This document contains too many pages.",
  documentByteLimit: "The combined image size exceeds the document limit.",
  documentPixelLimit: "The combined image resolution exceeds the document limit.",
  documentReloadNote: "Document access is kept only in this active page session; capability tokens are never placed in the URL.",
};

const ptBR: DocumentUiMessages = {
  selectedPages: (count) => `${count} ${count === 1 ? "página selecionada" : "páginas selecionadas"}`,
  movePageUp: (name) => `Mover ${name} para cima`,
  movePageDown: (name) => `Mover ${name} para baixo`,
  removePage: (name) => `Remover ${name}`,
  clearPages: "Limpar seleção",
  pageOrderHint: "A ordem exibida será a ordem do envio. Use os controles para alterá-la antes da análise.",
  analyzePages: (count) => count <= 1 ? "Analisar página" : `Analisar ${count} páginas`,
  pageOf: (current, total) => `Página ${current} de ${total}`,
  previousPage: "Anterior",
  nextPage: "Próxima",
  documentNavigation: "Páginas do documento",
  documentProgress: (completed, total) => `${completed} / ${total} páginas concluídas`,
  processingPage: "Processando",
  failedPage: "Falhou",
  readablePage: "Disponível",
  pageStatus: (page, status, readable) => `Página ${page}: ${readable ? "disponível" : status.replaceAll("_", " ")}`,
  documentUploadFailed: "Não foi possível criar o documento.",
  documentPageLimit: "Este documento contém páginas demais.",
  documentByteLimit: "O tamanho combinado das imagens excede o limite do documento.",
  documentPixelLimit: "A resolução combinada das imagens excede o limite do documento.",
  documentReloadNote: "O acesso ao documento existe somente nesta sessão ativa da página; capability tokens nunca são colocados na URL.",
};

export function documentMessagesFor(locale: UiLocale): DocumentUiMessages {
  return locale === "pt-BR" ? ptBR : en;
}
